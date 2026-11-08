import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {publishLinuxRuntimeCache} from '../linux_runtime_cache.mjs';

test('runtime cache publication requires successful native validation and the main action context', async t => {
    const root = await mkdtemp(join(tmpdir(), 'syncwatch-runtime-publish-'));
    t.after(() => rm(root, {recursive: true, force: true}));
    const previous = {...process.env};
    t.after(() => {process.env = previous;});
    const validation = join(root, 'artifacts/native/linux/payload');
    const directory = join(root, 'artifacts/application-ci/linux-x64');
    await mkdir(validation, {recursive: true});
    await mkdir(directory, {recursive: true});
    const archive = join(root, 'runtime.zip');
    await writeFile(archive, 'fixture archive');
    await writeFile(join(directory, 'runtime-cache-request.json'), JSON.stringify({archive, key: 'a'.repeat(64)}));
    const published = [];
    const publish = async options => {
        assert.equal(process.env.ACTIONS_RUNTIME_TOKEN, 'action-service-token');
        published.push(options);
    };
    process.env.GITHUB_EVENT_NAME = 'pull_request';
    process.env.GITHUB_REF = 'refs/heads/main';
    await publishLinuxRuntimeCache(root, publish);
    assert.equal(published.length, 0);
    process.env.GITHUB_EVENT_NAME = 'push';
    await writeFile(join(validation, 'build-manifest.json'), '{"validation":"failure"}');
    await publishLinuxRuntimeCache(root, publish);
    assert.equal(published.length, 0);
    await writeFile(join(validation, 'build-manifest.json'), '{"validation":"success"}');
    process.env.ACTIONS_RUNTIME_TOKEN = 'action-service-token';
    await publishLinuxRuntimeCache(root, publish);
    assert.deepEqual(published, [{archive, name: `syncwatch-dependency-cache-${'a'.repeat(64)}`}]);
});
