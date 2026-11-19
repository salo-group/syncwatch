"""Run two owned Meet windows in one disposable macOS test scope."""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import uuid


interrupted = False


def record_interruption(_number, _frame):
    global interrupted
    interrupted = True


def owned_commands(bundle):
    commands = subprocess.check_output(['/bin/ps', '-ww', '-axo', 'comm='], text=True, timeout=5)
    return any(command.startswith(str(bundle) + '/Contents/') for command in commands.splitlines())


def wait_for_exit(bundle):
    deadline = time.monotonic() + 10
    while owned_commands(bundle):
        if time.monotonic() >= deadline:
            raise RuntimeError('Browser helpers are still active')
        time.sleep(0.2)


def keychain(action, service):
    return subprocess.run(['/usr/bin/security', action, '-s', service, '-a', 'SyncWatch CEF research'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode


def run_phase(executable, scope, phase, timeout):
    if interrupted:
        raise InterruptedError('Research group interrupted')
    env = {key: value for key, value in os.environ.items() if not key.startswith('SYNCWATCH_')}
    env.update(SYNCWATCH_CEF_EXPERIMENT_ROOT=str(scope / 'cef-root'),
               SYNCWATCH_CEF_MEET_RESTART_PHASE=phase)
    with (scope / (phase + '.log')).open('wb') as log:
        with subprocess.Popen([str(executable)], cwd=scope, env=env, stdout=log, stderr=log,
                              stdin=subprocess.DEVNULL, start_new_session=True) as child:
            print(json.dumps({'phase': phase, 'pid': child.pid}), flush=True)
            try:
                deadline = time.monotonic() + timeout
                while child.poll() is None:
                    if interrupted:
                        raise InterruptedError('Research group interrupted')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Research window exceeded its lifetime')
                    time.sleep(0.2)
                status = child.returncode
            except (TimeoutError, InterruptedError):
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                raise
    if status != 0:
        raise RuntimeError(f'{phase} process exited with status {status}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    executable = bundle / 'Contents/MacOS/SyncWatchCallExperiment'
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                   check=True, timeout=20)
    if owned_commands(bundle):
        raise RuntimeError('An instance of this research bundle is already running')
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, record_interruption)

    scope_id = 'meet-restart-' + uuid.uuid4().hex
    service = 'org.salo.syncwatch.cef-research.' + scope_id
    if keychain('find-generic-password', service) != 44:
        raise RuntimeError('The disposable Keychain item must be absent at startup')
    parent = Path(tempfile.mkdtemp(prefix='syncwatch-meet-')).resolve()
    scope = parent / scope_id
    result = {'normalPhaseExits': [], 'ownedProcessesGone': False,
              'dedicatedKeyRemoved': False, 'profileRemoved': False}
    try:
        scope.mkdir(mode=0o700)
        for phase, timeout in [('create', 1800), ('reopen', 180)]:
            run_phase(executable, scope, phase, timeout)
            wait_for_exit(bundle)
            result['normalPhaseExits'].append(phase)
    finally:
        try:
            wait_for_exit(bundle)
            result['ownedProcessesGone'] = True
            try:
                keychain('delete-generic-password', service)
                result['dedicatedKeyRemoved'] = keychain('find-generic-password', service) == 44
            finally:
                shutil.rmtree(parent)
                result['profileRemoved'] = not parent.exists()
        finally:
            print(json.dumps(result), flush=True)
    if not result['dedicatedKeyRemoved']:
        raise RuntimeError('The disposable Keychain item could not be removed')


if __name__ == '__main__':
    main()
