"""Bounded raster decoding and public HTTPS-only fetching for avatars."""
import asyncio
import ipaddress
import socket
from io import BytesIO
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageOps

MAX_BYTES = 2 * 1024 * 1024


def public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_unspecified:
        raise ValueError('Image host must use a public address')
    if getattr(address, 'ipv4_mapped', None):
        public_address(str(address.ipv4_mapped))


def checked_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise ValueError('Invalid image URL')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443) or parts.fragment:
        raise ValueError('Use a public HTTPS image URL without credentials or redirects')
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        if '.' not in parts.hostname or parts.hostname.lower().endswith(('.localhost', '.local', '.internal')):
            raise ValueError('Image host must be public')
    else:
        public_address(parts.hostname)
    return url


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        result = []
        for af, _, proto, _, address in rows:
            public_address(address[0])
            result.append({'hostname':host, 'host':address[0], 'port':port, 'family':af, 'proto':proto, 'flags':socket.AI_NUMERICHOST})
        if not result:
            raise ValueError('Image host did not resolve')
        return result

    async def close(self):
        pass


async def fetch_image(url):
    checked_url(url)
    # The validating resolver supplies the exact connection addresses: no DNS-rebinding gap.
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False, limit=2)
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=12), trust_env=False, auto_decompress=False) as session:
        async with session.get(url, allow_redirects=False, headers={'Accept':'image/png,image/jpeg,image/webp','Accept-Encoding':'identity'}) as response:
            if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise ValueError('Image unavailable or redirects are not supported')
            if response.content_length is not None and response.content_length > MAX_BYTES:
                raise ValueError('Image must be at most 2 MiB')
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError('Image must be at most 2 MiB')
            return bytes(data)


def normalize_image(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Image must be at most 2 MiB')
    try:
        with Image.open(BytesIO(data), formats=['JPEG','PNG','WEBP']) as image:
            if max(image.size) > 4096 or image.width * image.height > 8_000_000 or min(image.size) < 1:
                raise ValueError('Image dimensions exceed 4096 px or 8 megapixels')
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Animated avatars are not supported')
            image.load()
            image = ImageOps.exif_transpose(image).convert('RGBA')
            image.thumbnail((512,512))
            # Copy pixels into a fresh image to discard all supplied metadata.
            clean = Image.new('RGBA', image.size)
            clean.paste(image)
            output = BytesIO()
            clean.save(output, format='PNG')
            return output.getvalue()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError('Use a valid static PNG, JPEG or WebP image') from exc
