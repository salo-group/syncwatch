import assert from 'node:assert/strict';
import {setTimeout} from 'node:timers/promises';
import test from 'node:test';
import {publisherSdks, restoreSdkGroups} from '../sdk_cache.mjs';

const selection = {groups: Array.from({length: 9}, (_, index) => ({
    key: String(index).repeat(64), sdk: 'jdk', archive: `.local/ci-sdk-cache/${index}.zip`,
}))};

test('cold SDK validation never reads a cache', async () => {
    const unexpected = () => {throw new Error('A cold build cannot restore a cache');};
    await restoreSdkGroups(selection, {cold: true}, unexpected);
    await restoreSdkGroups(selection, {cold: true, currentRun: true}, unexpected);
    await restoreSdkGroups({...selection, cold: true}, {}, unexpected);
});

test('SDK archive restoration overlaps at most four trusted-main requests', async () => {
    let active = 0, maximum = 0;
    const seen = [];
    await restoreSdkGroups(selection, {root: '/fixture', token: 'read-only-token'}, async config => {
        active++;
        maximum = Math.max(maximum, active);
        seen.push(config);
        await setTimeout(5);
        active--;
        return {hit: false};
    });
    assert.equal(maximum, 4);
    assert.equal(seen.length, 9);
    assert.equal(new Set(seen.map(item => item.name)).size, 9);
    assert.ok(seen.every(item => item.token === 'read-only-token'
        && /^syncwatch-dependency-cache-[0-9a-f]{64}$/.test(item.name)));
});

test('SDK transport failures propagate before provisioning', async () => {
    await assert.rejects(restoreSdkGroups(selection, {root: '/fixture'}, async () => {
        throw new Error('Digest mismatch');
    }), /Digest mismatch/);
});

test('native SDK consumers use exact-run transport instead of cross-run cache authorization', async () => {
    const seen = [];
    await restoreSdkGroups(selection, {root: '/fixture', currentRun: true}, async config => {
        seen.push(config);
        return {artifactId: 123};
    });
    assert.equal(seen.length, 9);
    for (const config of seen) {
        assert.equal(config.name, undefined);
        assert.match(config.prefix, /^syncwatch-sdk-[0-9a-f]{64}$/);
        assert.equal(config.sourceCommit, process.env.GITHUB_SHA);
    }
});

test('SDK cache publication has exactly one matrix producer per SDK', () => {
    assert.deepEqual(publisherSdks('kotlin-stdlib', 'linux-x64'), ['jdk', 'kotlin']);
    assert.deepEqual(publisherSdks('libvlc', 'windows-x64'), ['windows-vlc']);
    for (const component of ['compose-ui-jvm', 'jvm-annotations', 'skiko-native-objects']) {
        assert.deepEqual(publisherSdks(component, 'linux-x64'), []);
    }
    assert.deepEqual(publisherSdks('libvlc', 'macos-arm64'), []);
});

test('an unpublished oversized SDK group falls back to pinned vendor downloads', async () => {
    await restoreSdkGroups(selection, {root: '/fixture', currentRun: true}, async () => {
        throw new Error('Required current-run artifact is missing');
    });
    await assert.rejects(restoreSdkGroups(selection, {root: '/fixture', currentRun: true}, async () => {
        throw new Error('Artifact archive checksum does not match');
    }), /checksum/);
});
