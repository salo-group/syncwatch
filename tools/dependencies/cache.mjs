import {createHash} from 'node:crypto';
import {mkdir, writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';

const maximumBytes = 256 * 1024 * 1024;

export function trustedRun(run, repositoryId) {
    return run.status === 'completed' && run.conclusion === 'success'
        && ['push', 'schedule', 'workflow_dispatch'].includes(run.event)
        && run.head_branch === 'main' && run.repository?.id === repositoryId
        && run.head_repository?.id === repositoryId
        && run.path === '.github/workflows/repository.yml';
}

export async function restoreCache({repository, repositoryId, token, name, destination}, request = fetch) {
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)
        || !/^syncwatch-dependency-cache-[0-9a-f]{64}$/.test(name)
        || !Number.isSafeInteger(repositoryId) || !token) throw new Error('Invalid cache request');
    const base = `https://api.github.com/repos/${repository}`;
    async function api(path) {
        const result = await request(base + path, {
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
        if (!/^sha256:[0-9a-f]{64}$/.test(artifact.digest)
            || !Number.isSafeInteger(artifact.size_in_bytes) || artifact.size_in_bytes > maximumBytes) throw new Error('Invalid cache digest or size');
        const redirect = await api(`/actions/artifacts/${artifact.id}/zip`);
        if (redirect.status !== 302) throw new Error('Cache download redirect is missing');
        const location = new URL(redirect.headers.get('location'));
        if (location.protocol !== 'https:' || location.username || location.password) throw new Error('Cache storage requires HTTPS');
        // A storage request must never receive the repository token.
        const response = await request(location, {redirect: 'error', signal: AbortSignal.timeout(180000)});
        if (!response.ok || Number(response.headers.get('content-length')) > maximumBytes) throw new Error('Cache storage download failed');
        const chunks = [];
        let size = 0;
        for await (const chunk of response.body) {
            size += chunk.length;
            if (size > maximumBytes) throw new Error('Cache download exceeds its bound');
            chunks.push(chunk);
        }
        const bytes = Buffer.concat(chunks);
        if (bytes.length !== artifact.size_in_bytes
            || `sha256:${createHash('sha256').update(bytes).digest('hex')}` !== artifact.digest) throw new Error('Cache archive checksum does not match');
        await mkdir(dirname(destination), {recursive: true});
        await writeFile(destination, bytes, {flag: 'wx'});
        return {hit: true, artifactId: artifact.id, sourceCommit: run.head_sha};
    }
    return {hit: false};
}
