from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

import aiosqlite

from app import runtime
from app.admin_user_mutations import (
    remote_action,
    remote_tg,
    set_local_active,
    set_local_blocked,
    squad_ids,
)
from app.db import init_db, now_iso


class FakeClient:
    def __init__(self, major: int = 3) -> None:
        self.major = major
        self.calls: list[tuple[str, str, tuple[int, ...]]] = []

    async def api_major(self) -> int:
        return self.major

    async def _request(self, method: str, path: str, *, expected_status: tuple[int, ...], **kwargs):
        self.calls.append((method, path, expected_status))
        return 200, {'response': {}}


class AdminUserMutationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='admin-mutation-', suffix='.sqlite3')
        os.close(fd)
        await init_db(self.db_path)
        runtime.settings = SimpleNamespace(db_path=self.db_path)
        ts = now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO users (telegram_id,username,full_name,created_at,updated_at) VALUES (1001,'u','User',?,?)",
                (ts, ts),
            )
            cur = await db.execute("SELECT id FROM users WHERE telegram_id=1001")
            user_id = int((await cur.fetchone())[0])
            await db.execute(
                '''
                INSERT INTO plans
                    (slug,title,description,duration_days,traffic_gb,price_rub,is_active,sort_order,created_at,updated_at)
                VALUES ('p','Plan','',30,0,100,1,1,?,?)
                ''',
                (ts, ts),
            )
            cur = await db.execute("SELECT id FROM plans WHERE slug='p'")
            plan_id = int((await cur.fetchone())[0])
            await db.execute(
                '''
                INSERT INTO subscriptions
                    (user_id,telegram_id,plan_id,status,starts_at,expires_at,remnawave_user_id,subscription_url,traffic_limit_gb,created_at,updated_at)
                VALUES (?,1001,?,'active',?, '2099-01-01T00:00:00+00:00', '42', '',0,?,?)
                ''',
                (user_id, plan_id, ts, ts, ts),
            )
            await db.commit()

    async def asyncTearDown(self) -> None:
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_local_block_and_activate_roundtrip(self) -> None:
        self.assertEqual(await set_local_blocked(1001), 1)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT status FROM subscriptions WHERE telegram_id=1001 ORDER BY id DESC LIMIT 1")
            self.assertEqual((await cur.fetchone())[0], 'blocked')
        self.assertEqual(await set_local_active(1001), 1)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT status FROM subscriptions WHERE telegram_id=1001 ORDER BY id DESC LIMIT 1")
            self.assertEqual((await cur.fetchone())[0], 'active')

    async def test_v3_actions_use_official_action_endpoint(self) -> None:
        client = FakeClient(major=3)
        await remote_action(client, '42', 'disable')
        await remote_action(client, '42', 'enable')
        self.assertEqual(client.calls[0][0:2], ('POST', '/api/users/42/actions/disable'))
        self.assertEqual(client.calls[1][0:2], ('POST', '/api/users/42/actions/enable'))

    async def test_v3_rejects_non_numeric_user_id(self) -> None:
        client = FakeClient(major=3)
        with self.assertRaises(RuntimeError):
            await remote_action(client, 'not-a-number', 'disable')

    def test_identity_and_squad_parsing(self) -> None:
        self.assertEqual(remote_tg({'telegramId': 123}), 123)
        self.assertEqual(remote_tg({'email': 'tg456@bot.local'}), 456)
        self.assertEqual(
            squad_ids({'activeInternalSquads': ['a', {'uuid': 'b'}, {'id': 'c'}]}),
            {'a', 'b', 'c'},
        )


if __name__ == '__main__':
    unittest.main()
