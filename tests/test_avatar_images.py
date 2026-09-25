import asyncio
import socket
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch
from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from avatar_images import MAX_BYTES, PublicResolver, checked_url, normalize_image


def encoded(size=(24, 24), fmt='PNG', **kwargs):
    buffer = BytesIO()
    Image.new('RGB', size, 'red').save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


class ImageTests(unittest.TestCase):
    def test_valid_formats_and_resize(self):
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            with self.subTest(fmt=fmt):
                output = Image.open(BytesIO(normalize_image(encoded((1024, 256), fmt))))
                self.assertEqual(output.format, 'PNG')
                self.assertEqual(output.size, (512, 128))

    def test_metadata_removed(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('comment', '<script>alert(1)</script>')
        output = Image.open(BytesIO(normalize_image(encoded(pnginfo=metadata))))
        self.assertEqual(output.info, {})

    def test_reject_non_images_and_size(self):
        for data in (b'', b'<svg onload="alert(1)"></svg>', b'<html>error</html>', b'x'*(MAX_BYTES+1)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                normalize_image(data)

    def test_reject_dimensions_and_pixel_count(self):
        for size in ((4097, 1), (3000, 3000)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                normalize_image(encoded(size))

    def test_reject_animated_webp(self):
        buffer = BytesIO()
        Image.new('RGB', (4, 4), 'red').save(buffer, format='WEBP', save_all=True,
            append_images=[Image.new('RGB', (4, 4), 'blue')], duration=100, loop=0)
        with self.assertRaises(ValueError):normalize_image(buffer.getvalue())

    def test_reject_unsafe_urls(self):
        for url in ('http://example.com/a.png', 'file:///etc/passwd', 'https://127.0.0.1/a',
                    'https://10.0.0.1/a', 'https://169.254.169.254/a', 'https://[::1]/a',
                    'https://[::ffff:127.0.0.1]/a', 'https://localhost/a', 'https://x.local/a',
                    'https://u:p@example.com/a', 'https://example.com:8443/a', 'https://example.com/a#x',
                    'https://example.com/\na'):
            with self.subTest(url=url), self.assertRaises(ValueError):checked_url(url)

    def test_public_url(self):
        self.assertEqual(checked_url('https://cdn.example.com/a.png?size=512'), 'https://cdn.example.com/a.png?size=512')


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolver_pins_public_addresses(self):
        rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=rows)):
            resolved = await PublicResolver().resolve('cdn.example.com', 443)
        self.assertEqual(resolved[0]['host'], '8.8.8.8')

    async def test_reject_mixed_or_rebound_private_dns(self):
        rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in ('8.8.8.8', '192.168.1.1')]
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=rows)):
            with self.assertRaises(ValueError):await PublicResolver().resolve('cdn.example.com', 443)
