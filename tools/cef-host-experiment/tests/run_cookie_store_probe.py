"""Verify two full process lifetimes with one disposable synthetic profile."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess


def verify_encrypted_row(root: Path) -> bool:
    databases = list(root.glob('Default/**/Cookies'))
    if len(databases) != 1:
        raise RuntimeError('Expected one owned cookie database')
    with closing(sqlite3.connect(databases[0].as_uri() + '?mode=ro', uri=True)) as connection:
        rows = connection.execute('SELECT value, encrypted_value FROM cookies WHERE host_key = ? AND name = ?',
                                  ('syncwatch-cookie-probe.invalid', 'syncwatch_probe')).fetchall()
    if len(rows) != 1:
        raise RuntimeError('Expected one synthetic cookie')
    plaintext, ciphertext = rows[0]
    if plaintext or not ciphertext or b'synthetic-restart-marker' in ciphertext:
        raise RuntimeError('Synthetic cookie lacked encryption at rest')
    return ciphertext.startswith(b'v11')


def run_probe(executable: Path, scope: Path) -> dict:
    root = scope / 'cef-root'
    if not scope.is_dir() or scope.is_symlink() or root.exists() or root.is_symlink():
        raise ValueError('Use a new private scope for the synthetic profile')
    libsecret_prefix = False
    for mode in ('write', 'read'):
        result = subprocess.run(
            [str(executable)], cwd=scope, stdin=subprocess.DEVNULL, capture_output=True,
            env={**os.environ, 'SYNCWATCH_CEF_EXPERIMENT_ROOT': str(root),
                 'SYNCWATCH_CEF_COOKIE_PROBE_MODE': mode}, timeout=30,
        )
        evidence = f'CEF synthetic cookie probe {mode} passed'.encode('ascii')
        if result.returncode or evidence not in result.stderr.splitlines():
            raise RuntimeError(f'Probe {mode} failed: exit {result.returncode}')
        if mode == 'write':
            libsecret_prefix = verify_encrypted_row(root)
    return {'writeProcessExit': 0, 'readProcessExit': 0, 'encryptedRow': True,
            'libsecretVersionPrefix': libsecret_prefix}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('scope', type=Path)
    options = parser.parse_args()
    print(json.dumps(run_probe(options.executable.resolve(strict=True), options.scope.resolve(strict=True))))
