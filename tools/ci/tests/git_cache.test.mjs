import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {warmGitSources, publishGitSources, fetchGitCache} from '../git_cache.mjs';

const selected = {key: 'a'.repeat(64), archive: 'objects.zip', eligible: true, publisher: true, cold: false};

test('Git cache cold validation and empty source selections never read a remote cache', async () => {
    for (const selection of [{...selected, cold: true}, {...selected, eligible: false}]) {
        await warmGitSources({root: '/fixture'}, {
            run: () => JSON.stringify(selection),
            restore: () => assert.fail('A cold or empty selection cannot reuse Git objects'),
        });
    }
});

test('Git objects use the trusted-main transport and require fresh local verification', async () => {
    const operations = [];
    let request;
    await warmGitSources({root: '/fixture', token: 'fixture-token'}, {
        run: (_options, operation) => {
            operations.push(operation);
            return JSON.stringify(operation === 'plan' ? selected : {hit: true, restored: 2});
        },
        restore: async config => { request = config; return {hit: true}; },
    });
    assert.deepEqual(operations, ['plan', 'restore']);
    assert.equal(request.name, 'syncwatch-dependency-cache-' + selected.key);
    assert.equal(request.token, 'fixture-token');
    await assert.rejects(warmGitSources({root: '/fixture'}, {
        run: (_options, operation) => JSON.stringify(operation === 'plan' ? selected : {hit: false}),
        restore: async () => ({hit: true}),
    }), /did not validate/);
});

test('an unavailable cache API retains fresh pinned source preparation', async () => {
    const operations = [];
    const result = await warmGitSources({root: '/fixture'}, {
        run: (_options, operation) => { operations.push(operation); return JSON.stringify(selected); },
        restore: async () => { throw new Error('Cache API request failed'); },
    });
    assert.deepEqual(result, selected);
    assert.deepEqual(operations, ['plan']);
});

test('cache corruption remains fatal instead of falling back to source fetching', async () => {
    await assert.rejects(warmGitSources({root: '/fixture'}, {
        run: () => JSON.stringify(selected),
        restore: async () => { throw new Error('Artifact checksum does not match'); },
    }), /checksum does not match/);
});

test('an interrupted read-only connection retries the exact request before any response is used', async () => {
    const calls = [], pauses = [];
    const options = {redirect: 'manual', headers: {Authorization: 'fixture-secret'}};
    const response = new Response('{"artifacts":[]}', {status: 200});
    const actual = await fetchGitCache('https://api.github.com/repos/fixture/repo/actions/artifacts', options, {
        request: async (...arguments_) => {
            calls.push(arguments_);
            if (calls.length === 1) throw new TypeError('fetch failed', {cause: {code: 'UND_ERR_SOCKET'}});
            return response;
        },
        wait: async milliseconds => pauses.push(milliseconds),
    });
    assert.equal(actual, response);
    assert.deepEqual(calls, [[calls[0][0], options], [calls[0][0], options]]);
    assert.deepEqual(pauses, [500]);
});

test('connection retries are bounded and never replay a write', async () => {
    for (const method of ['GET', 'POST']) {
        let requests = 0;
        await assert.rejects(fetchGitCache('https://api.github.com/fixture', {method}, {
            request: async () => {
                requests++;
                throw new TypeError('fetch failed', {cause: {code: 'ECONNRESET'}});
            },
            wait: async () => {},
        }), /fetch failed/);
        assert.equal(requests, method === 'GET' ? 3 : 1);
    }
});

test('the default cache transport validates the complete request without exposing credentials', async () => {
    const previous = {fetch: globalThis.fetch, error: console.error,
        repository: process.env.GITHUB_REPOSITORY, id: process.env.GITHUB_REPOSITORY_ID};
    const requests = [], errors = [];
    try {
        process.env.GITHUB_REPOSITORY = 'fixture/repo';
        process.env.GITHUB_REPOSITORY_ID = '7';
        console.error = value => errors.push(value);
        globalThis.fetch = async (...arguments_) => {
            requests.push(arguments_);
            return new Response('{"artifacts":[]}', {status: 200});
        };
        await warmGitSources({root: '/fixture', token: 'fixture-secret'}, {
            run: () => JSON.stringify(selected),
        });
        assert.equal(requests.length, 1);
        assert.equal(requests[0][1].headers.Authorization, 'Bearer fixture-secret');
        await assert.rejects(warmGitSources({root: '/fixture', token: 'fixture-secret'}, {
            run: () => JSON.stringify({...selected, key: 'invalid-key'}),
        }), /Invalid cache request/);
        assert.equal(requests.length, 1);
        assert.ok(errors.some(value => value.includes('key=false')));
        assert.ok(errors.every(value => !value.includes('fixture-secret')));
    } finally {
        globalThis.fetch = previous.fetch;
        console.error = previous.error;
        for (const [key, value] of [['GITHUB_REPOSITORY', previous.repository], ['GITHUB_REPOSITORY_ID', previous.id]]) {
            if (value === undefined) delete process.env[key];
            else process.env[key] = value;
        }
    }
});

test('only the designated publisher can publish after a real archive round trip', async () => {
    const root = await mkdtemp(join(tmpdir(), 'sw-git-cache-'));
    try {
        await writeFile(join(root, selected.archive), 'verified fixture archive');
        const operations = [], publications = [];
        const dependencies = {
            run: (_options, operation) => operations.push(operation),
            publish: config => publications.push(config),
        };
        await publishGitSources({...selected, publisher: false}, {root, trusted: true}, dependencies);
        assert.deepEqual(operations, []);
        await publishGitSources(selected, {root, trusted: false}, dependencies);
        assert.deepEqual(operations, ['stage']);
        assert.deepEqual(publications, []);
        await publishGitSources(selected, {root, trusted: true}, dependencies);
        assert.equal(publications.length, 1);
        assert.equal(publications[0].name, 'syncwatch-dependency-cache-' + selected.key);
    } finally {
        await rm(root, {recursive: true, force: true});
    }
});
