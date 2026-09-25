import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from email_links import confirmation_link


class EmailLinkTests(unittest.TestCase):
    def test_default_template_resolves_domain_and_secret(self):
        self.assertEqual(confirmation_link('https://{domain}/auth/email?secret={secret}','hub.example.test','rp-token'),
                         'https://hub.example.test/auth/email?secret=rp-token')

    def test_custom_frontend_host_and_query_are_preserved(self):
        self.assertEqual(confirmation_link('https://accounts.example.test/verify?locale=zh&secret={secret}','api.example.test','ue-token'),
                         'https://accounts.example.test/verify?locale=zh&secret=ue-token')

    def test_query_value_is_encoded(self):
        self.assertEqual(confirmation_link('https://{domain}/auth/email?secret={secret}','hub.example.test','a&b#+'),
                         'https://hub.example.test/auth/email?secret=a%26b%23%2B')
