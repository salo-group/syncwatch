import {spawnSync} from 'node:child_process';
import {access} from 'node:fs/promises';
import {resolve} from 'node:path';
import {setTimeout as pause} from 'node:timers/promises';
import {restoreCache} from '../dependencies/cache.mjs';
import {main as upload} from './artifact_upload.mjs';

function runGitCache({root, component, platform, cold}, operation, capture = false) {
    const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
        !key.toUpperCase().startsWith('INPUT_') && !key.toUpperCase().startsWith('ACTIONS_')
        && key.toUpperCase() !== 'GITHUB_TOKEN'));
    const result = spawnSync(process.platform === 'win32' ? 'python' : 'python3',
        ['tools/ci/git_cache.py', operation, '--component', component, '--platform', platform,
            ...(cold ? ['--cold'] : [])], {cwd: root, env: environment, encoding: 'utf8',
            stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit', timeout: 900000, maxBuffer: 16 * 1024**2});
    if (result.status !== 0) {
        const code = /^[A-Z_]+$/.test(result.error?.code || '') ? result.error.code : 'none';
        console.error('Git cache ' + operation + ' subprocess failed: exit=' + result.status + '; code=' + code);
        throw new Error('Pinned Git source cache validation failed');
    }
    return result.stdout;
}

const TRANSIENT_NETWORK = new Set(['UND_ERR_SOCKET', 'UND_ERR_CONNECT_TIMEOUT', 'ECONNRESET',
    'ETIMEDOUT', 'EAI_AGAIN', 'ENOTFOUND']);

export async function fetchGitCache(url, options, {request = fetch, wait = pause} = {}) {
    for (let attempt = 0; ; attempt++) {
        try {
            const response = await request(url, options);
            if (!response.ok && response.status !== 302) {
                console.error('Git cache transport returned HTTP ' + response.status);
            }
            return response;
        } catch (error) {
            const code = error.cause?.code || error.code;
            const retry = TRANSIENT_NETWORK.has(code) && (options.method || 'GET') === 'GET'
                && !options.body && attempt < 2;
            console.error('Git cache connection failed: ' + (TRANSIENT_NETWORK.has(code) ? code : 'unclassified')
                + '; retry=' + retry);
            if (!retry) throw error;
            await wait(500 * (attempt + 1));
        }
    }
}

function restoreGitCache(config) {
    return restoreCache(config, fetchGitCache);
}

export async function warmGitSources(options, {restore = restoreGitCache, run = runGitCache} = {}) {
    console.log('Pinned Git object cache: preparing source identities');
    const selected = JSON.parse(run(options, 'plan', true));
    if (!selected.eligible || selected.cold || options.cold) return selected;
    console.log('Pinned Git object cache: looking up trusted main');
    let result;
    try {
        result = await restore({
            repository: process.env.GITHUB_REPOSITORY, repositoryId: Number(process.env.GITHUB_REPOSITORY_ID),
            token: options.token, name: 'syncwatch-dependency-cache-' + selected.key,
            destination: resolve(options.root, selected.archive),
        });
    } catch (error) {
        const known = new Set(['Invalid cache request', 'Cache API request failed', 'Invalid cache inventory',
            'Invalid cache identity', 'Invalid artifact digest or size', 'Artifact archive checksum does not match']);
        console.error('Git cache lookup failed: ' + (known.has(error.message) ? error.message : 'unclassified'));
        console.error('Git cache request checks: repository='
            + /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(process.env.GITHUB_REPOSITORY)
            + '; repositoryId=' + Number.isSafeInteger(Number(process.env.GITHUB_REPOSITORY_ID))
            + '; token=' + Boolean(options.token) + '; key=' + /^[0-9a-f]{64}$/.test(selected.key));
        // An unavailable lookup has supplied no Git objects. The normal source
        // initializer still fetches exact pins and runs every original check.
        if (error.message !== 'Cache API request failed') throw error;
        console.log('Pinned Git object cache API unavailable; fetching exact source pins instead');
        return selected;
    }
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
