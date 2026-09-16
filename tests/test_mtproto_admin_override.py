from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from app import runtime
from app.admin_db import init_admin_tables
from app.db import init_db
from app.products.mtproto.service import entitlement_for
from app.products.mtproto.storage import init_mtproto_tables


class MTProtoAdminOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mtproto-admin-', suffix='.sqlite3')
        os.close(fd)
        await init_db(self.db_path)
        await init_admin_tables(self.db_path)
        await init_mtproto_tables(self.db_path)
        self.old_settings = runtime.settings
        runtime.settings = SimpleNamespace(admin_ids=(1001,))

    async def asyncTearDown(self) -> None:
        runtime.settings = self.old_settings
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    async def test_admin_without_payment_is_entitled(self) -> None:
        entitlement = await entitlement_for(self.db_path, 1001)
        self.assertIsNotNone(entitlement)
        self.assertEqual(entitlement['plan_slug'], 'admin-override')
        self.assertEqual(int(entitlement['admin_override']), 1)

    async def test_non_admin_without_payment_is_not_entitled(self) -> None:
        self.assertIsNone(await entitlement_for(self.db_path, 2002))


if __name__ == '__main__':
    unittest.main()
