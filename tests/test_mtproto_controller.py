from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mtproto_control.app as control


SECRET_A = '0123456789abcdef0123456789abcdef'
SECRET_B = 'fedcba9876543210fedcba9876543210'


class MTProtoControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix='mtproto-control-')
        self.root = Path(self.tmp.name)
        self.old = {
            'DATA_DIR': control.DATA_DIR,
            'STATE_FILE': control.STATE_FILE,
            'SECRETS_FILE': control.SECRETS_FILE,
            'ACK_FILE': control.ACK_FILE,
            'DRY_RUN': control.DRY_RUN,
            'APPLY_MODE': control.APPLY_MODE,
            'APPLY_TIMEOUT': control.APPLY_TIMEOUT,
            'APPLY_COMMAND': control.APPLY_COMMAND,
        }
        control.DATA_DIR = self.root
        control.STATE_FILE = self.root / 'users.json'
        control.SECRETS_FILE = self.root / 'active_secrets.txt'
        control.ACK_FILE = self.root / 'active_secrets.applied.sha256'
        control.DRY_RUN = False
        control.APPLY_MODE = 'watch'
        control.APPLY_TIMEOUT = 1.0
        control.APPLY_COMMAND = ''

    async def asyncTearDown(self) -> None:
        for key, value in self.old.items():
            setattr(control, key, value)
        self.tmp.cleanup()

    async def test_manifest_contains_only_enabled_secrets(self) -> None:
        control._persist_state({
            '1001': {'secret': SECRET_A, 'enabled': True},
            '1002': {'secret': SECRET_B, 'enabled': False},
        })
        self.assertEqual(control.SECRETS_FILE.read_text(encoding='utf-8'), SECRET_A + '\n')

    async def test_watch_mode_waits_for_matching_ack(self) -> None:
        control._persist_state({'1001': {'secret': SECRET_A, 'enabled': True}})
        digest = control._manifest_digest()
        control.ACK_FILE.write_text(digest + '\n', encoding='utf-8')
        ok, detail = await control._apply_backend()
        self.assertTrue(ok)
        self.assertEqual(detail, 'manifest-applied')

    async def test_state_file_keeps_disabled_credential_without_exposing_it_to_manifest(self) -> None:
        state = {'1001': {'secret': SECRET_A, 'enabled': False}}
        control._persist_state(state)
        loaded = control._load_state()
        self.assertEqual(loaded['1001']['secret'], SECRET_A)
        self.assertEqual(control.SECRETS_FILE.read_text(encoding='utf-8'), '')


if __name__ == '__main__':
    unittest.main()
