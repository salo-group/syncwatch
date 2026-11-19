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


def run_probe(executable: Path, scope: Path, phase: str = 'both') -> dict:
    root = scope / 'cef-root'
    if not scope.is_dir() or scope.is_symlink() or root.is_symlink():
        raise ValueError('Use a private scope for the synthetic profile')
    if phase not in ('both', 'write', 'read', 'expect-unreadable'):
        raise ValueError('Unexpected synthetic probe phase')
    if phase in ('read', 'expect-unreadable'):
        marker = root / '.syncwatch-cookie-probe'
        if not root.is_dir() or not marker.is_file() or marker.is_symlink():
            raise ValueError('The scope has no synthetic probe profile')
        libsecret_prefix = verify_encrypted_row(root)
    elif root.exists():
        raise ValueError('Use a new private scope to write the synthetic profile')
    expect_unreadable = phase == 'expect-unreadable'
    modes = ('write', 'read') if phase == 'both' else ('read' if expect_unreadable else phase,)
    result = {}
    for mode in modes:
        process = subprocess.run(
            [str(executable)], cwd=scope, stdin=subprocess.DEVNULL, capture_output=True,
            env={**os.environ, 'SYNCWATCH_CEF_EXPERIMENT_ROOT': str(root),
                 'SYNCWATCH_CEF_COOKIE_PROBE_MODE': mode}, timeout=30,
        )
        outcome = 'failed' if expect_unreadable else 'passed'
        evidence = f'CEF synthetic cookie probe {mode} {outcome}'.encode('ascii')
        expected_exit = 1 if expect_unreadable else 0
        if process.returncode != expected_exit or evidence not in process.stderr.splitlines():
            raise RuntimeError(f'Probe {mode} failed: exit {process.returncode}')
        result[f'{mode}ProcessExit'] = expected_exit
        if mode == 'write':
            libsecret_prefix = verify_encrypted_row(root)
    return {**result, 'encryptedRow': True, 'libsecretVersionPrefix': libsecret_prefix}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('scope', type=Path)
    parser.add_argument('--phase', choices=('both', 'write', 'read', 'expect-unreadable'), default='both')
    options = parser.parse_args()
    print(json.dumps(run_probe(options.executable.resolve(strict=True), options.scope.absolute(), options.phase)))
