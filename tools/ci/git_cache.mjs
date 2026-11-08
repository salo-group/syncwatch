import {spawnSync} from 'node:child_process';
import {access} from 'node:fs/promises';
import {resolve} from 'node:path';
import {restoreCache} from '../dependencies/cache.mjs';
import {main as upload} from './artifact_upload.mjs';

function runGitCache({root, component, platform, cold}, operation, capture = false) {
    const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
        !key.toUpperCase().startsWith('INPUT_') && !key.toUpperCase().startsWith('ACTIONS_')
        && key.toUpperCase() !== 'GITHUB_TOKEN'));
    const result = spawnSync(process.platform === 'win32' ? 'python' : 'python3',
        ['tools/ci/git_cache.py', operation, '--component', component, '--platform', platform,
            ...(cold ? ['--cold'] : [])], {cwd: root, env: environment, encoding: 'utf8',
            stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit', timeout: 900000});
    if (result.status !== 0) throw new Error('Pinned Git source cache validation failed');
    return result.stdout;
}

export async function warmGitSources(options, {restore = restoreCache, run = runGitCache} = {}) {
    const selected = JSON.parse(run(options, 'plan', true));
    if (!selected.eligible || selected.cold || options.cold) return selected;
    const result = await restore({
        repository: process.env.GITHUB_REPOSITORY, repositoryId: Number(process.env.GITHUB_REPOSITORY_ID),
        token: options.token, name: 'syncwatch-dependency-cache-' + selected.key,
        destination: resolve(options.root, selected.archive),
    });
    if (result.hit) {
        const restored = JSON.parse(run(options, 'restore', true));
        if (!restored.hit) throw new Error('Restored Git cache did not validate');
        console.log('Pinned Git object cache: verified hit; source checkouts restored: ' + restored.restored);
    } else {
        console.log('Pinned Git object cache: miss');
    }
    return selected;
}

export async function publishGitSources(selected, options, {run = runGitCache, publish = upload} = {}) {
    // Every source key has one platform publisher. PRs exercise the real archive
    // round trip but cannot publish a cache that later runs would trust.
    if (!selected.eligible || !selected.publisher || process.exitCode) return;
    run(options, 'stage');
    const archive = resolve(options.root, selected.archive);
    if (options.trusted && await access(archive).then(() => true, () => false)) {
        await publish({archive, name: 'syncwatch-dependency-cache-' + selected.key});
    }
}
