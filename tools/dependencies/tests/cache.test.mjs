import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {restoreCache, trustedRun} from '../cache.mjs';

const repositoryId = 123;
const name = `syncwatch-dependency-cache-${'a'.repeat(64)}`;
const bytes = Buffer.from('prepared fixture archive');
const validRun = {status: 'completed', conclusion: 'success', event: 'push', head_branch: 'main', repository: {id: repositoryId}, head_repository: {id: repositoryId}, path: '.github/workflows/repository.yml', head_sha: 'b'.repeat(40)};
const validArtifact = {id: 1, name, expired: false, workflow_run: {id: 2, head_branch: 'main', head_repository_id: repositoryId}, digest: `sha256:${createHash('sha256').update(bytes).digest('hex')}`, size_in_bytes: bytes.length};
const json = value => new Response(JSON.stringify(value), {status: 200});

async function fixture(t) {
    const root = await mkdtemp(join(tmpdir(), 'syncwatch-cache-'));
    t.after(() => rm(root, {recursive: true, force: true}));
    return {repository: 'salo-group/syncwatch', repositoryId, token: 'private-read-token', name, destination: join(root, 'cache.zip')};
}

function service({artifacts = [validArtifact], run = validRun, data = bytes, redirect = 'https://storage.example.invalid/archive?private-signature'} = {}) {
    const calls = [];
    return {calls, request: async (url, options) => {
        calls.push({url: String(url), ...options});
        if (String(url).includes('/actions/artifacts?')) return json({artifacts});
        if (String(url).includes('/actions/runs/')) return json(run);
        if (String(url).endsWith('/zip')) return new Response(null, {status: 302, headers: {location: redirect}});
        return new Response(data, {status: 200});
    }};
}

test('only successful canonical main workflows can supply a cache', () => {
    assert.equal(trustedRun(validRun, repositoryId), true);
    for (const delta of [{event: 'pull_request'}, {event: 'pull_request_target'}, {conclusion: 'failure'}, {status: 'in_progress'}, {head_branch: 'feature'}, {head_repository: {id: 999}}, {repository: {id: 999}}, {path: '.github/workflows/other.yml'}]) {
        assert.equal(trustedRun({...validRun, ...delta}, repositoryId), false);
    }
});

test('cache round-trip verifies archive digest and never sends the repository token to storage', async t => {
    const config = await fixture(t);
    const server = service();
    assert.deepEqual(await restoreCache(config, server.request), {hit: true, artifactId: 1, sourceCommit: validRun.head_sha});
    assert.deepEqual(await readFile(config.destination), bytes);
    assert.equal(server.calls.length, 4);
    for (const call of server.calls.slice(0, 3)) assert.equal(call.headers.Authorization, 'Bearer private-read-token');
    assert.equal(server.calls[3].headers, undefined);
    assert.equal(server.calls[3].redirect, 'error');
});

test('PR, expired, and failed-run artifacts are misses rather than reusable cache entries', async t => {
    const config = await fixture(t);
    for (const artifact of [{...validArtifact, expired: true}, {...validArtifact, workflow_run: {...validArtifact.workflow_run, head_repository_id: 999}}, {...validArtifact, workflow_run: {...validArtifact.workflow_run, head_branch: 'feature'}}]) {
        const server = service({artifacts: [artifact]});
        assert.deepEqual(await restoreCache(config, server.request), {hit: false});
        assert.equal(server.calls.length, 1);
    }
    const server = service({run: {...validRun, event: 'pull_request'}});
    assert.deepEqual(await restoreCache(config, server.request), {hit: false});
    assert.equal(server.calls.length, 2);
});

test('corruption, missing digests, unsafe storage redirects, and API errors fail', async t => {
    const config = await fixture(t);
    for (const server of [service({data: Buffer.from('corrupt')}), service({artifacts: [{...validArtifact, digest: undefined}]}), service({redirect: 'http://storage.example.invalid/archive'}), service({redirect: 'https://user:secret@storage.example.invalid/archive'})]) {
        await assert.rejects(restoreCache(config, server.request));
    }
    await assert.rejects(restoreCache(config, async () => new Response(null, {status: 403})));
    await assert.rejects(readFile(config.destination));
});

test('invalid request metadata and oversized archives fail before storage writes', async t => {
    const config = await fixture(t);
    for (const delta of [{repository: '../other'}, {repositoryId: NaN}, {token: ''}, {name: 'latest'}]) {
        await assert.rejects(restoreCache({...config, ...delta}, async () => {throw new Error('Must not request');}));
    }
    const server = service({artifacts: [{...validArtifact, size_in_bytes: 300 * 1024 * 1024}]});
    await assert.rejects(restoreCache(config, server.request));
    assert.equal(server.calls.length, 2);
});
