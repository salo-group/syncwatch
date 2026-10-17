import {createHash} from 'node:crypto';
import {appendFile, readFile, realpath, stat} from 'node:fs/promises';
import {isAbsolute, relative, resolve} from 'node:path';

const service = 'github.actions.results.api.v1.ArtifactService';
const maximumBytes = 256 * 1024 * 1024;

class ArtifactUploadError extends Error {}

export function backendIds(token) {
    try {
        const parts = token.split('.');
        if (parts.length !== 3) throw new ArtifactUploadError();
        const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
        const scope = claims.scp.split(' ').find(value => value.startsWith('Actions.Results:'));
        const match = /^Actions\.Results:([0-9a-f-]{36}):([0-9a-f-]{36})$/i.exec(scope);
        if (!match) throw new ArtifactUploadError();
        return {workflow_run_backend_id: match[1], workflow_job_run_backend_id: match[2]};
    } catch {
        throw new ArtifactUploadError('Artifact runtime scope is unavailable');
    }
}

function httpsUrl(value) {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password) {
        throw new ArtifactUploadError('Artifact service requires HTTPS');
    }
    return url;
}

export async function uploadArtifact({archive, name, workspace, token, resultsUrl, retentionDays = 7}, request = fetch) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(name)) {
        throw new ArtifactUploadError('Invalid artifact name');
    }
    if (!Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 7) {
        throw new ArtifactUploadError('Artifact retention must be between one and seven days');
    }
    const root = await realpath(workspace);
    const path = await realpath(resolve(root, archive));
    const member = relative(root, path);
    if (isAbsolute(member) || member === '..' || member.startsWith('../') || member.startsWith('..\\')) {
        throw new ArtifactUploadError('Artifact archive must remain inside the workspace');
    }
    const metadata = await stat(path);
    if (!metadata.isFile() || metadata.size < 4 || metadata.size > maximumBytes) {
        throw new ArtifactUploadError('Artifact archive has an invalid size');
    }
    const bytes = await readFile(path);
    if (!bytes.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 0x03, 0x04]))) {
        throw new ArtifactUploadError('Artifact input must be a prepared ZIP archive');
    }
    const ids = backendIds(token);
    const base = httpsUrl(resultsUrl);
    const digest = createHash('sha256').update(bytes).digest('hex');
    async function rpc(method, payload) {
        const url = new URL(`/twirp/${service}/${method}`, base);
        const response = await request(url, {
            method: 'POST', redirect: 'error', signal: AbortSignal.timeout(60000),
            headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
            body: JSON.stringify({...ids, name, ...payload}),
        });
        if (!response.ok) throw new ArtifactUploadError(`${method} HTTP ${response.status}`);
        const result = await response.json();
        if (result.ok !== true) throw new ArtifactUploadError(`${method} did not succeed`);
        return result;
    }
    const created = await rpc('CreateArtifact', {
        version: 7, mime_type: 'application/zip',
        expires_at: new Date(Date.now() + retentionDays * 86400000).toISOString(),
    });
    const uploadUrl = httpsUrl(created.signed_upload_url);
    const uploaded = await request(uploadUrl, {
        method: 'PUT', redirect: 'error', signal: AbortSignal.timeout(180000),
        headers: {'Content-Type': 'application/zip', 'x-ms-blob-type': 'BlockBlob', 'x-ms-version': '2023-11-03'},
        body: bytes,
    });
    if (!uploaded.ok) throw new ArtifactUploadError(`Artifact storage HTTP ${uploaded.status}`);
    const finalized = await rpc('FinalizeArtifact', {size: String(bytes.length), hash: `sha256:${digest}`});
    if (!/^[1-9][0-9]*$/.test(finalized.artifact_id)) throw new ArtifactUploadError('Artifact ID is missing');
    return {id: finalized.artifact_id, digest, size: bytes.length};
}

export async function main() {
    try {
        const result = await uploadArtifact({
            archive: process.env.INPUT_ARCHIVE,
            name: process.env.INPUT_NAME,
            workspace: process.env.GITHUB_WORKSPACE,
            token: process.env.ACTIONS_RUNTIME_TOKEN,
            resultsUrl: process.env.ACTIONS_RESULTS_URL,
            retentionDays: Math.min(7, Number(process.env.GITHUB_RETENTION_DAYS || 7)),
        });
        const url = `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}/artifacts/${result.id}`;
        console.log(`Stored artifact ${result.id}: ${result.size} bytes, sha256:${result.digest}`);
        if (process.env.GITHUB_STEP_SUMMARY) {
            await appendFile(process.env.GITHUB_STEP_SUMMARY, `\n[Native build artifact](${url})\n\nSHA-256: \`${result.digest}\`\n`);
        }
    } catch (error) {
        // Runtime tokens, service response bodies, and signed URLs must never enter logs.
        const detail = error instanceof ArtifactUploadError ? error.message : 'runtime or network request failed';
        console.error(`::error::Native artifact upload failed: ${detail}`);
        process.exitCode = 1;
    }
}
