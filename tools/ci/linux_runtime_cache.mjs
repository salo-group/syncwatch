import {spawnSync} from 'node:child_process';
import {access, readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {restoreCache} from '../dependencies/cache.mjs';
import {main as upload} from './artifact_upload.mjs';

export async function publishLinuxRuntimeCache(root, publish = upload) {
    const trusted = ['push', 'schedule', 'workflow_dispatch'].includes(process.env.GITHUB_EVENT_NAME)
        && process.env.GITHUB_REF === 'refs/heads/main';
    if (!trusted) return;
    const validation = JSON.parse(await readFile(resolve(root, 'artifacts/native/linux/payload/build-manifest.json'), 'utf8'));
    if (validation.validation !== 'success') return;
    const request = JSON.parse(await readFile(resolve(root, 'artifacts/application-ci/linux-x64/runtime-cache-request.json'), 'utf8'));
    if (await access(request.archive).then(() => true, () => false)) {
        await publish({archive: request.archive, name: `syncwatch-dependency-cache-${request.key}`});
    }
}

async function main() {
    const [operation, path] = process.argv.slice(2);
    if (operation !== 'restore' || !path) throw new Error('Invalid runtime cache operation');
    const root = process.env.GITHUB_WORKSPACE || process.cwd();
    const request = JSON.parse(await readFile(resolve(path), 'utf8'));
    const present = () => access(request.archive).then(() => true, () => false);
    if (!request.cold && !await present()) {
        await restoreCache({repository: process.env.GITHUB_REPOSITORY,
            repositoryId: Number(process.env.GITHUB_REPOSITORY_ID), token: process.env.GITHUB_TOKEN,
            name: `syncwatch-dependency-cache-${request.key}`, destination: request.archive});
    }
    const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
        !key.startsWith('INPUT_') && !key.startsWith('ACTIONS_') && key !== 'GITHUB_TOKEN'));
    const result = spawnSync('python3', ['tools/ci/linux_runtime_cache.py', 'restore', path],
        {cwd: root, env: environment, encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'], timeout: 300000});
    if (result.status !== 0) throw new Error('Runtime cache verification failed');
    const restored = JSON.parse(result.stdout);
    console.error(`Source-built Linux runtime cache: ${restored.hit ? 'verified hit' : 'cold build'}`);
    console.log(JSON.stringify(restored));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        await main();
    } catch {
        console.error('::error::Source-built Linux runtime cache failed; inspect input and receipt validation');
        process.exitCode = 1;
    }
}
