import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import ip_location as geo


class LocationTests(unittest.TestCase):
    def setUp(self):
        geo.lookup.cache_clear()
        self.reader = Mock()
        self.reader.get.return_value = {
            'country': {'iso_code': 'CN', 'names': {'en': 'China', 'zh-CN': '中国'}},
            'subdivisions': [{'names': {'en': 'Guangdong', 'zh-CN': '广东省'}}],
        }
        self.mock = patch.object(geo, 'database', return_value=self.reader)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        geo.lookup.cache_clear()

    def test_province_bilingual(self):
        self.assertEqual(geo.location_for_ip('1.2.3.4', language='zh-CN'), '中国 / 广东省')
        self.assertEqual(geo.location_for_ip('1.2.3.4'), 'China / Guangdong')

    def test_missing_subdivision_keeps_country(self):
        self.reader.get.return_value.pop('subdivisions')
        self.assertEqual(geo.location_for_ip('1.2.3.4'), 'China')

    def test_ipv6_and_mapped_ipv4(self):
        self.assertEqual(geo.country_for_ip('2606:4700:4700::1111'), 'CN')
        self.assertEqual(geo.country_for_ip('::ffff:1.2.3.4'), 'CN')
        self.reader.get.assert_called_with('1.2.3.4')

    def test_private_ipv6_and_invalid_do_not_query_database(self):
        for value in ('::1', 'fd00::1234', 'fe80::abcd', '192.168.2.3'):
            self.assertEqual(geo.country_for_ip(value), '00')
        self.assertEqual(geo.country_for_ip('invalid'), 'XX')
        self.reader.get.assert_not_called()

    def test_unknown_is_concise(self):
        self.reader.get.return_value = None
        self.assertEqual(geo.location_for_ip('1.2.3.4'), '-*-*-')

    def test_missing_database_does_not_break_login(self):
        with patch.object(geo, 'database', return_value=None):
            self.assertEqual(geo.country_for_ip('1.2.3.4'), 'XX')

    def test_untrusted_country_header_ignored(self):
        request = SimpleNamespace(headers={'cf-ipcountry': 'US'}, client=SimpleNamespace(host='1.2.3.4'))
        with patch.dict(os.environ, {'TRUST_CF_IPCOUNTRY': ''}):
            self.assertEqual(geo.request_country(request), 'CN')
        with patch.dict(os.environ, {'TRUST_CF_IPCOUNTRY': 'true'}):
            self.assertEqual(geo.request_country(request), 'US')

    def test_no_mixed_country_and_province(self):
        self.assertEqual(geo.location_for_ip('1.2.3.4', country='US'), 'United States')

    def test_unknown_to_known_does_not_revoke_same_country(self):
        self.assertFalse(geo.country_changed('XX', 'CN', '1.2.3.4'))
        self.assertTrue(geo.country_changed('XX', 'US', '1.2.3.4'))
        self.assertTrue(geo.country_changed('CN', 'US', '1.2.3.4'))
        self.assertFalse(geo.country_changed('CN', 'XX', '1.2.3.4'))


if __name__ == '__main__':
    unittest.main()
