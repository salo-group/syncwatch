import {setTimeout as pause} from 'node:timers/promises';
import {archiveMetadata, downloadArchive} from '../ci/artifact_download.mjs';

const transientHttpStatuses = new Set([502, 503, 504]);
const retryDelays = [250, 750];

export function trustedRun(run, repositoryId) {
    return run.status === 'completed' && run.conclusion === 'success'
        && ['push', 'schedule', 'workflow_dispatch'].includes(run.event)
        && run.head_branch === 'main' && run.repository?.id === repositoryId
        && run.head_repository?.id === repositoryId
        && run.path === '.github/workflows/repository.yml';
}

export async function restoreCache({repository, repositoryId, token, name, destination}, request = fetch, wait = pause) {
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)
        || !/^syncwatch-dependency-cache-[0-9a-f]{64}$/.test(name)
        || !Number.isSafeInteger(repositoryId) || !token) throw new Error('Invalid cache request');
    const base = `https://api.github.com/repos/${repository}`;
    async function read(url, options) {
        for (const delay of retryDelays) {
            const response = await request(url, options);
            if (!transientHttpStatuses.has(response.status)) return response;
            await response.body?.cancel();
            await wait(delay, undefined, {signal: options.signal});
        }
        return request(url, options);
    }
    async function api(path) {
        const result = await read(base + path, {
            redirect: 'manual', signal: AbortSignal.timeout(60000),
            headers: {Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'},
        });
        if (!result.ok && result.status !== 302) throw new Error('Cache API request failed');
        return result;
    }
    // A bounded search can miss an old cache, producing a cold build rather than unsafe reuse.
    const listing = await (await api(`/actions/artifacts?name=${name}&per_page=100`)).json();
    if (!Array.isArray(listing.artifacts)) throw new Error('Invalid cache inventory');
    for (const artifact of [...listing.artifacts].sort((a, b) => b.id - a.id)) {
        if (artifact.name !== name || artifact.expired || artifact.workflow_run?.head_branch !== 'main'
            || artifact.workflow_run?.head_repository_id !== repositoryId) continue;
        if (!Number.isSafeInteger(artifact.id) || !Number.isSafeInteger(artifact.workflow_run?.id)) throw new Error('Invalid cache identity');
        const run = await (await api(`/actions/runs/${artifact.workflow_run.id}`)).json();
        if (!trustedRun(run, repositoryId)) continue;
        archiveMetadata(artifact);
        await downloadArchive({redirect: await api(`/actions/artifacts/${artifact.id}/zip`), artifact, destination}, read);
        return {hit: true, artifactId: artifact.id, sourceCommit: run.head_sha};
    }
    return {hit: false};
}
