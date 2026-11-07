import {spawnSync} from 'node:child_process';
import {access, readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {restoreCache} from '../dependencies/cache.mjs';
import {main as upload} from './artifact_upload.mjs';

const [operation, path] = process.argv.slice(2);
try {
    if (!['restore', 'publish'].includes(operation) || !path) throw new Error('Invalid runtime cache operation');
    const root = process.env.GITHUB_WORKSPACE || process.cwd();
    const request = JSON.parse(await readFile(resolve(path), 'utf8'));
    const present = () => access(request.archive).then(() => true, () => false);
    if (operation === 'restore') {
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
    } else {
        const trusted = ['push', 'schedule', 'workflow_dispatch'].includes(process.env.GITHUB_EVENT_NAME)
            && process.env.GITHUB_REF === 'refs/heads/main';
        if (trusted && await present()) {
            await upload({archive: request.archive, name: `syncwatch-dependency-cache-${request.key}`});
        }
    }
} catch {
    console.error('::error::Source-built Linux runtime cache failed; inspect input and receipt validation');
    process.exitCode = 1;
}
