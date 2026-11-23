import assert from 'node:assert/strict';
import test from 'node:test';
import {publishesDependencyCache, runDependencies} from '../../../.github/actions/dependency-build/build.mjs';

test('a shared macOS runner builds each component with the selected cold mode', async () => {
    const calls = [];
    await runDependencies('["zlib","fastjsonschema"]', 'macos-arm64', 'true', async (...args) => {
        calls.push(args);
    });
    assert.deepEqual(calls, [['zlib', 'macos-arm64', true], ['fastjsonschema', 'macos-arm64', true]]);
});

test('a failed build prevents the shared dependency job from succeeding', async () => {
    const calls = [];
    await assert.rejects(runDependencies('["zlib","fastjsonschema"]', 'macos-arm64', 'false', async name => {
        calls.push(name);
        throw new Error('build failed');
    }), /build failed/);
    assert.deepEqual(calls, ['zlib']);
});

test('invalid selections are rejected before any build starts', async () => {
    let calls = 0;
    const build = async () => { calls++; };
    for (const components of ['[]', '["zlib","zlib"]', '["zlib","../outside"]', '["zlib",null]']) {
        await assert.rejects(runDependencies(components, 'macos-arm64', 'false', build));
    }
    await assert.rejects(runDependencies('["zlib","fastjsonschema"]', 'windows-x64', 'false', build));
    assert.equal(calls, 0);
});

test('a rerun leaves immutable shared cache names to the first attempt', () => {
    const first = {GITHUB_EVENT_NAME: 'push', GITHUB_REF: 'refs/heads/main', GITHUB_RUN_ATTEMPT: '1'};
    assert.equal(publishesDependencyCache(first), true);
    assert.equal(publishesDependencyCache({...first, GITHUB_RUN_ATTEMPT: '2'}), false);
    assert.equal(publishesDependencyCache({...first, GITHUB_EVENT_NAME: 'pull_request'}), false);
    assert.equal(publishesDependencyCache({...first, GITHUB_REF: 'refs/heads/feature'}), false);
});
