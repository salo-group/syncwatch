import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {backendIds, main, uploadArtifact} from '../artifact_upload.mjs';

const run = 'ce7f54c7-61c7-4aae-887f-30da475f5f1a';
const job = 'ca395085-040a-526b-2ce8-bdc85f692774';
const token = `header.${Buffer.from(JSON.stringify({scp: `Actions.Results:${run}:${job}`})).toString('base64url')}.signature`;
const signedUrl = 'https://storage.example.invalid/artifact.zip?sig=private-signature';

async function fixture(t) {
    const directory = await mkdtemp(join(tmpdir(), 'syncwatch-artifact-'));
    t.after(() => rm(directory, {recursive:true, force:true}));
    const archive = join(directory, 'artifact.zip');
    await writeFile(archive, Buffer.from([0x50, 0x4b, 0x03, 0x04, 1, 2, 3]));
    return {archive, workspace:directory, name:'syncwatch-apple-test', token, resultsUrl:'https://results.example.invalid'};
}

function transport(calls, fail = '') {
    return async (url, options) => {
        calls.push({url:String(url), ...options});
        if (String(url).endsWith('CreateArtifact')) {
            return Response.json({ok:fail !== 'create', signed_upload_url:signedUrl});
        }
        if (options.method === 'PUT') return new Response('', {status:fail === 'storage' ? 503 : 201});
        return Response.json({ok:fail !== 'finalize', artifact_id:'12345'});
    };
}

test('runtime scope supplies both backend IDs without treating claims as authorization', () => {
    assert.deepEqual(backendIds(token), {workflow_run_backend_id:run, workflow_job_run_backend_id:job});
    for (const value of ['', 'bad.token.value', 'a.e30.b']) assert.throws(() => backendIds(value));
});

test('upload creates, transfers, and finalizes exact bytes with bounded retention and digest', async t => {
    const input = await fixture(t);
    const calls = [];
    const result = await uploadArtifact(input, transport(calls));
    assert.equal(calls.length, 3);
    const created = JSON.parse(calls[0].body);
    assert.equal(created.workflow_run_backend_id, run);
    assert.equal(created.workflow_job_run_backend_id, job);
    assert.equal(created.name, input.name);
    assert.equal(created.mime_type, 'application/zip');
    assert.equal(created.version, 7);
    assert.ok(Math.abs(Date.parse(created.expires_at) - Date.now() - 7 * 86400000) < 5000);
    assert.deepEqual(calls[1].body, await readFile(input.archive));
    assert.equal(calls[1].headers.Authorization, undefined);
    assert.equal(calls[1].redirect, 'error');
    assert.equal(calls[1].headers['x-ms-blob-type'], 'BlockBlob');
    assert.equal(result.digest, createHash('sha256').update(calls[1].body).digest('hex'));
    assert.equal(result.id, '12345');
    assert.deepEqual(JSON.parse(calls[2].body), {
        workflow_run_backend_id:run, workflow_job_run_backend_id:job, name:input.name,
        size:String(result.size), hash:`sha256:${result.digest}`,
    });
});

test('a failed creation, transfer, or finalization cannot report success', async t => {
    for (const failure of ['create', 'storage', 'finalize']) {
        const calls = [];
        await assert.rejects(uploadArtifact(await fixture(t), transport(calls, failure)));
        assert.equal(calls.length, {create:1, storage:2, finalize:3}[failure]);
    }
});

test('HTTP errors and unexpected artifact IDs fail closed', async t => {
    const input = await fixture(t);
    await assert.rejects(uploadArtifact(input, async () => new Response('', {status:403})), /HTTP 403/);
    let index = 0;
    await assert.rejects(uploadArtifact(input, async () => {
        index += 1;
        if (index === 1) return Response.json({ok:true, signed_upload_url:signedUrl});
        if (index === 2) return new Response('', {status:201});
        return Response.json({ok:true});
    }), /ID is missing/);
});

test('archive inputs cannot escape the workspace or use a symlink outside it', async t => {
    const input = await fixture(t);
    const other = await fixture(t);
    await assert.rejects(uploadArtifact({...input, archive:other.archive}), /inside the workspace/);
    const link = join(input.workspace, 'link.zip');
    await symlink(other.archive, link);
    await assert.rejects(uploadArtifact({...input, archive:link}), /inside the workspace/);
});

test('invalid archive, names, transport schemes, and retention fail before upload', async t => {
    const input = await fixture(t);
    for (const changed of [{name:'../bad'}, {retentionDays:0}, {retentionDays:8}, {resultsUrl:'http://example.invalid'}, {resultsUrl:'https://user:secret@example.invalid'}]) {
        await assert.rejects(uploadArtifact({...input, ...changed}, () => { throw new Error('No request should occur'); }));
    }
    await writeFile(input.archive, 'not a ZIP file');
    await assert.rejects(uploadArtifact(input), /ZIP archive/);
});

test('signed storage URLs must remain HTTPS', async t => {
    await assert.rejects(uploadArtifact(await fixture(t), async () => Response.json({ok:true, signed_upload_url:'http://example.invalid/private'})), /HTTPS/);
});

test('action failure logs cannot disclose runtime tokens or signed storage URLs', async t => {
    const input = await fixture(t);
    const keys = {
        INPUT_ARCHIVE:input.archive, INPUT_NAME:input.name, GITHUB_WORKSPACE:input.workspace,
        ACTIONS_RUNTIME_TOKEN:token, ACTIONS_RESULTS_URL:input.resultsUrl,
    };
    const old = Object.fromEntries(Object.keys(keys).map(key => [key, process.env[key]]));
    const previousExit = process.exitCode;
    const messages = [];
    t.mock.method(console, 'error', message => messages.push(message));
    t.mock.method(globalThis, 'fetch', async () => { throw new Error(token + signedUrl); });
    try {
        Object.assign(process.env, keys);
        await main();
        assert.equal(process.exitCode, 1);
        assert.ok(messages.join('').includes('failed'));
        assert.ok(!messages.join('').includes(token));
        assert.ok(!messages.join('').includes('private-signature'));
    } finally {
        process.exitCode = previousExit;
        for (const [key, value] of Object.entries(old)) {
            if (value === undefined) delete process.env[key];
            else process.env[key] = value;
        }
    }
});
