from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.db import init_db, now_iso
from app.products.mtproto.storage import (
    ensure_access_row,
    get_access,
    get_paid_entitlement,
    init_mtproto_tables,
    rotate_generation,
    set_plan_enabled,
)


class MTProtoStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mtproto-test-', suffix='.sqlite3')
        os.close(fd)
        await init_db(self.db_path)
        await init_mtproto_tables(self.db_path)
        ts = now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO plans
                    (slug,title,description,duration_days,traffic_gb,price_rub,is_active,sort_order,created_at,updated_at,mtproto_enabled)
                VALUES ('paid','Paid','',30,0,199,1,10,?,?,1)
            ''', (ts, ts))
            await db.execute('''
                INSERT INTO users (telegram_id,username,full_name,created_at,updated_at)
                VALUES (1001,'user1001','User',?,?)
            ''', (ts, ts))
            await db.commit()

    async def asyncTearDown(self) -> None:
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def _create_subscription(self, *, paid: bool) -> int:
        ts = now_iso()
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute('SELECT id FROM users WHERE telegram_id=1001')
            user_id = int((await cur.fetchone())[0])
            cur = await db.execute("SELECT id FROM plans WHERE slug='paid'")
            plan_id = int((await cur.fetchone())[0])
            cur = await db.execute('''
                INSERT INTO subscriptions
                    (user_id,telegram_id,plan_id,status,starts_at,expires_at,remnawave_user_id,subscription_url,traffic_limit_gb,created_at,updated_at)
                VALUES (?,1001,?,'active',?,?, '', '',0,?,?)
            ''', (user_id, plan_id, ts, expires, ts, ts))
            subscription_id = int(cur.lastrowid)
            if paid:
                await db.execute('''
                    INSERT INTO payments
                        (provider,provider_payment_id,user_id,telegram_id,plan_id,amount_rub,currency,status,payment_url,payload,created_at,updated_at,paid_at,subscription_id)
                    VALUES ('stars',?, ?,1001,?,199,'XTR','paid','','',?,?,?,?,?)
                ''', (f'p-{subscription_id}', user_id, plan_id, ts, ts, ts, subscription_id))
            await db.commit()
        return subscription_id

    async def test_paid_active_enabled_plan_is_entitled(self) -> None:
        await self._create_subscription(paid=True)
        entitlement = await get_paid_entitlement(self.db_path, 1001)
        self.assertIsNotNone(entitlement)
        self.assertEqual(entitlement['plan_slug'], 'paid')

    async def test_without_successful_payment_is_not_entitled(self) -> None:
        await self._create_subscription(paid=False)
        self.assertIsNone(await get_paid_entitlement(self.db_path, 1001))

    async def test_plan_toggle_revokes_entitlement(self) -> None:
        await self._create_subscription(paid=True)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT id FROM plans WHERE slug='paid'")
            plan_id = int((await cur.fetchone())[0])
        await set_plan_enabled(self.db_path, plan_id, False)
        self.assertIsNone(await get_paid_entitlement(self.db_path, 1001))

    async def test_generation_rotation_is_stable(self) -> None:
        row = await ensure_access_row(self.db_path, 1001)
        self.assertEqual(int(row['generation']), 1)
        rotated = await rotate_generation(self.db_path, 1001)
        self.assertEqual(int(rotated['generation']), 2)
        stored = await get_access(self.db_path, 1001)
        self.assertEqual(int(stored['generation']), 2)


if __name__ == '__main__':
    unittest.main()
