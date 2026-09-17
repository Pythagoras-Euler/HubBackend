"""Independent logo/background layers and cache keys for generated banners."""
import hashlib
import json
from PIL import Image, ImageOps


def cache_key(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def compose_background(background, logo, opacity):
    base = Image.new('RGBA', (1700, 300), 'white')
    try:
        opacity = max(0.0, min(1.0, float(opacity)))
    except (ValueError, TypeError):
        opacity = 0.15
    if background is not None:
        layer = ImageOps.fit(background.convert('RGBA'), base.size, method=Image.Resampling.LANCZOS)
        layer.putalpha(layer.getchannel('A').point(lambda value: round(value * opacity)))
        base.alpha_composite(layer)
    if logo is not None:
        mark = ImageOps.contain(logo.convert('RGBA'), (200, 200), method=Image.Resampling.LANCZOS)
        base.alpha_composite(mark, (1475 + (200-mark.width)//2, 25 + (200-mark.height)//2))
    return base.convert('RGB')
