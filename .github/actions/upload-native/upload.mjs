import {main} from '../../../tools/ci/artifact_upload.mjs';
import {nativeApplicationMaximumBytes} from '../../../tools/ci/artifact_policy.mjs';
import {publishLinuxRuntimeCache} from '../../../tools/ci/linux_runtime_cache.mjs';

await main({maximumBytes: nativeApplicationMaximumBytes});
if (!process.exitCode && process.env.INPUT_ARCHIVE === 'artifacts/native/linux/artifact.zip') {
    await publishLinuxRuntimeCache(process.env.GITHUB_WORKSPACE);
}
