import {spawnSync} from 'node:child_process';
import {access, appendFile, readFile} from 'node:fs/promises';
import {delimiter, dirname, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {restoreCache} from '../../../tools/dependencies/cache.mjs';
import {main as upload} from '../../../tools/ci/artifact_upload.mjs';
import {warmSdkArchives, publishSdkArchives} from '../../../tools/ci/sdk_cache.mjs';
import {warmGitSources, publishGitSources} from '../../../tools/ci/git_cache.mjs';

async function buildComponent(component, platform, cold) {
    const root = process.env.GITHUB_WORKSPACE;
    const directory = resolve(root, 'artifacts/dependencies', component, platform);
    const dependencyArchive = resolve(directory, 'artifact.zip');
    const artifactSuffix = `${platform}-${process.env.GITHUB_SHA}-${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}`;
    const python = process.platform === 'win32' ? 'python' : 'python3';
    function runCommand(command) {
        const environment = {...process.env};
        // Use the action's declared Node 24 toolchain for source-only design builds.
        environment.PATH = dirname(process.execPath) + delimiter + process.env.PATH;
        for (const key of Object.keys(environment)) {
            if (key.startsWith('INPUT_') || key.startsWith('ACTIONS_') || key === 'GITHUB_TOKEN') delete environment[key];
        }
        const result = spawnSync(command[0], command.slice(1), {
            cwd: root, stdio: 'inherit', env: environment, timeout: 15000000,
        });
        return result.status === 0;
    }
    const run = operation => runCommand([python, 'tools/dependencies/build.py', operation,
        '--component', component, '--platform', platform, ...(cold ? ['--cold'] : []),
        ...(operation === 'build' && !cold ? ['--reuse-prepared-sources'] : [])]);
    if (component === 'libvlc' && platform === 'windows-x64'
        && !runCommand([python, '-X', 'utf8=0', '-m', 'unittest', 'discover',
            '-s', 'tools/ci/tests', '-p', 'test_*cache.py', '-v'])) {
        throw new Error('Windows CI cache regressions failed before SDK provisioning');
    }
    if (component === 'syncplay-runtime' && !runCommand([python, '-m', 'unittest', 'discover',
        '-s', 'tools/bridge/tests', '-p', 'test_build.py', '-v'])) {
        throw new Error('Native helper producer regressions failed before SDK provisioning');
    }
    const registry = JSON.parse(await readFile(resolve(root, 'tools/dependencies/components.json'), 'utf8'));
    const target = registry.components[component]?.targets[platform];
    if (!target) throw new Error('Unregistered dependency target');
    const sdks = ['jdk', 'kotlin'].filter(sdk => Object.hasOwn(target.toolchains, sdk));
    if (component === 'libvlc' && platform === 'windows-x64') sdks.push('windows-vlc');
    const sdkSelection = sdks.length ? await warmSdkArchives({
        root, platform, sdks, cold, token: process.env['INPUT_CACHE-TOKEN'],
    }) : null;
    for (const sdk of ['jdk', 'kotlin']) {
        if (Object.hasOwn(target.toolchains, sdk) && !runCommand([python, ...sdkSelection.commands[sdk]])) {
            throw new Error(`Pinned developer ${sdk} provisioning failed`);
        }
    }
    if (component === 'syncplay-runtime' && !runCommand([python, 'tools/syncplay-runtime/provision_sdks.py'])) {
        throw new Error('Headless engine developer SDK provisioning failed');
    }
    if (['libvlc', 'skiko-native-objects'].includes(component) && !runCommand([python, 'tools/vlc/provision_sdks.py'])) {
        throw new Error('Native media developer SDK provisioning failed');
    }
    const gitSelection = await warmGitSources({root, component, platform, cold, token: process.env['INPUT_CACHE-TOKEN']});
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
    await access(dependencyArchive);
    await upload({archive: dependencyArchive, name: `syncwatch-dependency-${component}-${artifactSuffix}`});
    if (!succeeded) throw new Error('Dependency build failed');
    if (component === 'libvlc') {
        // Decoder SDK acceptance and application consumers have separate reports.
        const provisioned = runCommand([python, 'tools/playback/ci.py', 'provision']);
        const accepted = provisioned && runCommand([python, 'tools/playback/ci.py', 'check']);
        const playbackArchive = resolve(root, 'artifacts/playback', platform + '.zip');
        if (await access(playbackArchive).then(() => true, () => false)) {
            await upload({archive: playbackArchive, name: `syncwatch-playback-${artifactSuffix}`});
        }
        if (!accepted) throw new Error('Native playback consumer acceptance failed');
    }
    if (component === 'syncplay-runtime') {
        if (!runCommand([python, 'tools/bridge/ci.py', 'provision'])
            || !runCommand([python, 'tools/bridge/ci.py', 'check'])) {
            throw new Error('Owned helper and native pipe acceptance failed');
        }
        await upload({archive: resolve(root, 'artifacts/helper', platform + '.zip'), name: `syncwatch-helper-${artifactSuffix}`});
    }
    if (component === 'design-generator') {
        if (!runCommand([python, 'tools/design/resources.py', '--check'])
            || !runCommand([process.execPath, '--test', 'tools/design/tests/resources.test.mjs'])) {
            throw new Error('Native design resource validation failed');
        }
    }
    const trusted = ['push', 'schedule', 'workflow_dispatch'].includes(process.env.GITHUB_EVENT_NAME)
        && process.env.GITHUB_REF === 'refs/heads/main';
    if (trusted && !process.exitCode) {
        await upload({archive: dependencyArchive, name: `syncwatch-dependency-cache-${prepared.key}`});
    }
    if (sdkSelection && !process.exitCode) await publishSdkArchives(sdkSelection, {root, component, platform, trusted});
    if (!process.exitCode) await publishGitSources(gitSelection, {root, component, platform, cold, trusted});
    if (process.env.GITHUB_STEP_SUMMARY) {
        await appendFile(process.env.GITHUB_STEP_SUMMARY, `\nVerified dependency: ${component} / ${platform}\n\nInput key: \`${prepared.key}\`\n\nCold requested: ${cold}\n`);
    }
}

export async function runDependencies(componentsInput, platform, coldInput, build = buildComponent) {
    const components = JSON.parse(componentsInput);
    if (!['linux-x64', 'macos-arm64', 'windows-x64'].includes(platform)
        || !['true', 'false'].includes(coldInput) || !Array.isArray(components) || !components.length
        || components.length > (platform === 'macos-arm64' ? 16 : 1)
        || components.some(component => typeof component !== 'string' || !/^[a-z][a-z0-9-]{0,47}$/.test(component))
        || new Set(components).size !== components.length) throw new Error('Invalid dependency job');
    for (const component of components) {
        console.log(`Verifying dependency ${component} / ${platform}`);
        await build(component, platform, coldInput === 'true');
        if (process.exitCode) throw new Error('Dependency publication failed');
    }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    try {
        await runDependencies(process.env.INPUT_COMPONENTS, process.env.INPUT_PLATFORM, process.env.INPUT_COLD);
    } catch {
        // Never expose service errors, runtime identities, tokens, or signed URLs.
        console.error('::error::Dependency job failed; inspect the validation stage and retained report');
        process.exitCode = 1;
    }
}
