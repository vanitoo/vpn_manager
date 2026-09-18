from __future__ import annotations

import unittest
from types import SimpleNamespace

from app import runtime
from app.products.mtproto.service import configuration_error, public_secret


BASE_SECRET = '0123456789abcdef0123456789abcdef'


class MTProtoSecretFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_settings = runtime.settings
        runtime.settings = SimpleNamespace(
            admin_ids=(),
            mtproto_enabled=True,
            mtproto_secret_key='test-master-key',
            mtproto_public_host='proxy.example.com',
            mtproto_public_port=443,
            mtproto_dry_run=True,
            mtproto_control_url='',
            mtproto_secret_mode='faketls',
            mtproto_fake_tls_domain='www.cloudflare.com',
            mtproto_random_padding=True,
        )

    def tearDown(self) -> None:
        runtime.settings = self.old_settings

    def test_faketls_secret_matches_mtproxy_domain_format(self) -> None:
        expected = 'ee' + BASE_SECRET + 'www.cloudflare.com'.encode('utf-8').hex()
        self.assertEqual(public_secret(BASE_SECRET), expected)
        self.assertEqual(configuration_error(), '')

    def test_random_padding_secret(self) -> None:
        runtime.settings.mtproto_secret_mode = 'random_padding'
        self.assertEqual(public_secret(BASE_SECRET), 'dd' + BASE_SECRET)

    def test_plain_secret(self) -> None:
        runtime.settings.mtproto_secret_mode = 'plain'
        self.assertEqual(public_secret(BASE_SECRET), BASE_SECRET)

    def test_faketls_rejects_url_instead_of_hostname(self) -> None:
        runtime.settings.mtproto_fake_tls_domain = 'https://www.cloudflare.com/'
        self.assertIn('hostname only', configuration_error())


if __name__ == '__main__':
    unittest.main()
