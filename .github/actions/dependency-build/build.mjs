import {spawnSync} from 'node:child_process';
import {access, appendFile, readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {restoreCache} from '../../../tools/dependencies/cache.mjs';
import {main as upload} from '../../../tools/ci/artifact_upload.mjs';

async function main() {
    const component = process.env.INPUT_COMPONENT;
    const platform = process.env.INPUT_PLATFORM;
    const cold = process.env.INPUT_COLD === 'true';
    if (!/^[a-z][a-z0-9-]{0,47}$/.test(component)
        || !['linux-x64', 'macos-arm64', 'windows-x64'].includes(platform)) throw new Error('Invalid dependency job');
    const root = process.env.GITHUB_WORKSPACE;
    const directory = resolve(root, 'artifacts/dependencies', component, platform);
    const python = process.platform === 'win32' ? 'python' : 'python3';
    function run(operation) {
        const environment = {...process.env};
        for (const key of Object.keys(environment)) {
            if (key.startsWith('INPUT_') || key.startsWith('ACTIONS_') || key === 'GITHUB_TOKEN') delete environment[key];
        }
        const result = spawnSync(python, ['tools/dependencies/build.py', operation, '--component', component, '--platform', platform, ...(cold ? ['--cold'] : [])], {
            cwd: root, stdio: 'inherit', env: environment, timeout: 15000000,
        });
        return result.status === 0;
    }
    if (!run('prepare')) throw new Error('Dependency preparation failed');
    const prepared = JSON.parse(await readFile(resolve(directory, 'request.json'), 'utf8'));
    const present = await access(prepared.cacheFile).then(() => true, () => false);
    if (!cold && !present) {
        const restored = await restoreCache({
            repository: process.env.GITHUB_REPOSITORY, repositoryId: Number(process.env.GITHUB_REPOSITORY_ID),
            token: process.env['INPUT_CACHE-TOKEN'], name: `syncwatch-dependency-cache-${prepared.key}`, destination: prepared.cacheFile,
        });
        console.log(`Trusted dependency cache: ${restored.hit ? 'downloaded and digest-verified' : 'miss'}`);
    }
    const succeeded = run('build');
    await access(resolve(directory, 'artifact.zip'));
    process.env.INPUT_ARCHIVE = resolve(directory, 'artifact.zip');
    process.env.INPUT_NAME = `syncwatch-dependency-${component}-${platform}-${process.env.GITHUB_SHA}-${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}`;
    await upload();
    if (!succeeded) throw new Error('Dependency build failed');
    const trusted = ['push', 'schedule', 'workflow_dispatch'].includes(process.env.GITHUB_EVENT_NAME)
        && process.env.GITHUB_REF === 'refs/heads/main';
    if (trusted && !process.exitCode) {
        process.env.INPUT_NAME = `syncwatch-dependency-cache-${prepared.key}`;
        await upload();
    }
    if (process.env.GITHUB_STEP_SUMMARY) {
        await appendFile(process.env.GITHUB_STEP_SUMMARY, `\nVerified dependency: ${component} / ${platform}\n\nInput key: \`${prepared.key}\`\n\nCold requested: ${cold}\n`);
    }
}

try {
    await main();
} catch {
    // Never expose service errors, runtime identities, tokens, or signed URLs.
    console.error('::error::Dependency job failed; inspect the validation stage and retained report');
    process.exitCode = 1;
}
