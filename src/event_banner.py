"""Read legacy event heading images without changing stored descriptions."""
import os
import re
from urllib.parse import urljoin, urlsplit


EVENT_IMAGE = re.compile(r'^\[Image src="([^"\r\n]+)"\]')


def event_banner_url(description, config):
    match = EVENT_IMAGE.match(description or "")
    if match is None:
        return None
    value = match.group(1).strip()
    if not value or re.search(r'[\s\x00-\x1f\x7f\\<>"`]', value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
            return None
        if parsed.scheme and not parsed.netloc:
            return None
        if not parsed.scheme:
            if value.startswith(("?", "#")):
                return None
            base = os.environ.get("HUB_PUBLIC_URL", "https://" + config.domain).rstrip("/") + "/"
            value = urljoin(base, value)
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        parsed.port  # Reject malformed or out-of-range ports.
        return value
    except ValueError:
        return None
