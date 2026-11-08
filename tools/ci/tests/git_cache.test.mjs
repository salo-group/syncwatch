import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {warmGitSources, publishGitSources} from '../git_cache.mjs';

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
