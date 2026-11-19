"""Run the native profile policy without loading CEF or visiting Google."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def protect_scope(path):
    if os.name != 'nt':
        path.chmod(0o700)
        return
    script = r'''
$ErrorActionPreference = 'Stop'
$owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetOwner($owner)
$acl.SetAccessRuleProtection($true,$false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $owner,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $env:SYNCWATCH_TEST_PROFILE -AclObject $acl
'''
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   env={**os.environ, 'SYNCWATCH_TEST_PROFILE': str(path)}, check=True, timeout=20)


def main(executable):
    with tempfile.TemporaryDirectory(prefix='syncwatch-profile-policy-') as directory:
        parent = Path(directory).resolve()
        scope = parent / 'meet-restart-0123456789abcdef0123456789abcdef'
        scope.mkdir()
        if os.name != 'nt':
            scope.chmod(0o755)
        subprocess.run([executable, str(scope), 'reject'], check=True, timeout=10)
        protect_scope(scope)
        subprocess.run([executable, scope.name, 'reject'], check=True, timeout=10, cwd=parent)
        wrong_name = parent / 'meet-restart-invalid'
        wrong_name.mkdir(mode=0o700)
        protect_scope(wrong_name)
        subprocess.run([executable, str(wrong_name), 'reject'], check=True, timeout=10)
        if os.name != 'nt':
            alias_parent = parent / 'alias'
            alias_parent.symlink_to(parent, target_is_directory=True)
            subprocess.run([executable, str(alias_parent / scope.name), 'reject'],
                           check=True, timeout=10)
            alias_parent.unlink()
        subprocess.run([executable, str(scope)], check=True, timeout=10)


if __name__ == '__main__':
    main(str(Path(sys.argv[1]).resolve(strict=True)))
