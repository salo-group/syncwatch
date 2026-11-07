import {spawnSync} from 'node:child_process';
import {access} from 'node:fs/promises';
import {resolve} from 'node:path';
import {parseArgs} from 'node:util';
import {pathToFileURL} from 'node:url';
import {restoreCache} from '../dependencies/cache.mjs';
import {restoreRunArtifact} from './artifact_download.mjs';
import {main as upload} from './artifact_upload.mjs';

export async function restoreSdkGroups(selection, {root, token, cold = false, currentRun = false},
    restore = currentRun ? restoreRunArtifact : restoreCache) {
    if (cold || selection.cold) return;
    let next = 0, hits = 0;
    await Promise.all(Array.from({length: Math.min(4, selection.groups.length)}, async () => {
        while (next < selection.groups.length) {
            const group = selection.groups[next++];
            const config = {
                repository: process.env.GITHUB_REPOSITORY,
                repositoryId: Number(process.env.GITHUB_REPOSITORY_ID), token,
                destination: resolve(root, group.archive),
                ...(currentRun ? {
                    runId: Number(process.env.GITHUB_RUN_ID), attempt: Number(process.env.GITHUB_RUN_ATTEMPT),
                    sourceCommit: process.env.GITHUB_SHA, prefix: `syncwatch-sdk-${group.key}`,
                } : {name: `syncwatch-dependency-cache-${group.key}`}),
            };
            let result;
            try {
                result = await restore(config);
            } catch (error) {
                // An oversized SDK group can be downloaded from its pinned vendor
                // source instead. Corrupt or ambiguous published artifacts still fail.
                if (!currentRun || error.message !== 'Required current-run artifact is missing') throw error;
                result = {hit: false};
            }
            if (result.hit || result.artifactId) hits++;
        }
    }));
    console.log(`Developer SDK archive groups: ${hits}/${selection.groups.length} restored from `
        + (currentRun ? 'this workflow run' : 'trusted main'));
}

export async function warmSdkArchives({root, platform, sdks, token, cold = false, currentRun = false}) {
    const python = process.platform === 'win32' ? 'python' : 'python3';
    const options = ['--platform', platform, ...sdks.flatMap(sdk => ['--sdk', sdk])];
    const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
        !key.startsWith('INPUT_') && !key.startsWith('ACTIONS_') && key !== 'GITHUB_TOKEN'));
    function run(operation, capture = false) {
        const result = spawnSync(python, ['tools/ci/sdk_cache.py', operation, ...options,
            ...(cold ? ['--cold'] : [])], {cwd: root, env: environment,
            stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit', encoding: 'utf8', timeout: 1200000});
        if (result.status !== 0) throw new Error('Developer SDK archive preparation failed');
        return result.stdout;
    }
    const selection = JSON.parse(run('plan', true));
    await restoreSdkGroups(selection, {root, token, cold, currentRun});
    run('materialize');
    return selection;
}

export function publisherSdks(component, platform) {
    // Each key has one producer per workflow run, avoiding artifact-name races.
    if (component === 'kotlin-stdlib' && platform === 'linux-x64') return ['jdk', 'kotlin'];
    if (component === 'libvlc' && platform === 'windows-x64') return ['windows-vlc'];
    return [];
}

export async function publishSdkArchives(selection, {root, component, platform, trusted}) {
    const sdks = publisherSdks(component, platform);
    for (const group of selection.groups.filter(group => sdks.includes(group.sdk))) {
        const archive = resolve(root, group.archive);
        if (await access(archive).then(() => true, () => false)) {
            await upload({archive, name: `syncwatch-sdk-${group.key}-${process.env.GITHUB_SHA}`
                + `-${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}`});
            if (trusted && !process.exitCode) {
                await upload({archive, name: `syncwatch-dependency-cache-${group.key}`});
            }
        }
    }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const {values} = parseArgs({options: {
            platform: {type: 'string'}, sdk: {type: 'string', multiple: true},
            'current-run': {type: 'boolean', default: false},
        }});
        await warmSdkArchives({root: process.env.GITHUB_WORKSPACE || process.cwd(),
            platform: values.platform, sdks: values.sdk || [], token: process.env.GITHUB_TOKEN,
            currentRun: values['current-run']});
    } catch {
        console.error('::error::Developer SDK archive cache failed; inspect the pinned-input validation');
        process.exitCode = 1;
    }
}
