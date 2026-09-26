import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from email_content import render_email_html


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.targets = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.targets.append(dict(attrs).get('href'))


class EmailContentTests(unittest.TestCase):
    link = 'https://hub.example/auth/email?secret=rp-test&locale=zh'

    def test_existing_plain_placeholder_becomes_clickable(self):
        html = render_email_html('Verify:<br>{link}', self.link)
        self.assertEqual(Links(html).targets, [self.link])

    def test_custom_anchor_is_not_nested(self):
        html = render_email_html('<a class="button" href="{link}">{link}</a>', self.link)
        self.assertEqual(Links(html).targets, [self.link])
        self.assertIn('class="button"', html)

    def test_button_label_and_other_html_are_preserved(self):
        template = '<!DOCTYPE html><!-- note --><p>A &amp; B &#39;</p><a href="{link}">Verify email</a>'
        html = render_email_html(template, self.link)
        self.assertEqual(Links(html).targets, [self.link])
        self.assertIn('>Verify email</a>', html)
        self.assertTrue(html.startswith('<!DOCTYPE html><!-- note --><p>A &amp; B &#39;</p>'))

    def test_link_cannot_inject_html_or_attributes(self):
        link = self.link + '\"><img src=x>'
        html = render_email_html('{link}', link)
        self.assertEqual(Links(html).targets, [link])
        self.assertNotIn('<img', html)

    def test_templates_without_placeholder_remain_unchanged(self):
        self.assertEqual(render_email_html('<p>Custom message</p>', self.link), '<p>Custom message</p>')


if __name__ == '__main__':
    unittest.main()
