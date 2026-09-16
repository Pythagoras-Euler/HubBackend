"""Version 1 delivery identifiers. Persist results; never recalculate on reads.

Eight Crockford characters: keyed UTC month (2), CSPRNG body (5), checksum (1).
This is an input aid and an opaque label, not an authorization mechanism.
"""
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAX_ATTEMPTS = 16


def normalize_public_id(value):
    return value.strip().upper() if isinstance(value, str) and value.isascii() else ""


def checksum(payload):
    """Odd positional weights modulo 32 detect EVERY single-symbol change.

    Not all transpositions are detected. Deliberately stays in our 32 symbols
    rather than Crockford's extended mod-37 check alphabet.
    """
    if len(payload) != 7 or any(c not in ALPHABET for c in payload):
        raise ValueError("Expected seven Crockford Base32 symbols")
    return ALPHABET[sum((2 * i + 1) * ALPHABET.index(c)
                        for i, c in enumerate(payload)) % 32]


def validate_public_id(value):
    value = normalize_public_id(value)
    return (len(value) == 8 and all(c in ALPHABET for c in value)
            and hmac.compare_digest(checksum(value[:7]), value[7]))


def business_date(value):
    """ISO dates, UTC epoch seconds, or datetimes; never fall back to now."""
    if value is None or isinstance(value, bool):
        raise ValueError("Missing business date")
    try:
        if isinstance(value, (int, float)):
            if value <= 0:
                raise ValueError("Missing business date")
            value = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).date()
        if isinstance(value, date):
            return value
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("Invalid business date") from exc
    raise ValueError("Missing or invalid business date")


def base32(value, width):
    return "".join(ALPHABET[(value >> (5 * i)) & 31] for i in reversed(range(width)))


@dataclass(frozen=True)
class PublicIDs:
    epoch: str
    key: bytes

    def __post_init__(self):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", self.epoch):
            raise ValueError("VTCHUB_PUBLIC_ID_EPOCH must be YYYY-MM")
        date.fromisoformat(self.epoch + "-01")
        if len(self.key) < 32:
            raise ValueError("VTCHUB_PUBLIC_ID_TIME_KEY must contain at least 32 bytes")
        if self.epoch_number + 1023 > 9999 * 12 + 11:
            raise ValueError("Epoch cannot represent all 1024 months")

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("VTCHUB_PUBLIC_ID_EPOCH", ""),
                   os.environ.get("VTCHUB_PUBLIC_ID_TIME_KEY", "").encode())

    @property
    def epoch_number(self):
        year, month = map(int, self.epoch.split("-"))
        return year * 12 + month - 1

    @property
    def fingerprint(self):
        return hmac.new(self.key, ("vtchub-public-id:v1:" + self.epoch).encode(),
                        hashlib.sha256).hexdigest()

    def _round(self, right, round_number):
        message = b"vtchub:month:v1:" + bytes([round_number, right])
        return hmac.new(self.key, message, hashlib.sha256).digest()[0] & 31

    def _permute(self, value, inverse=False):
        if not 0 <= value < 1024:
            raise ValueError("Month is outside the 1024-month epoch range")
        left, right = value >> 5, value & 31
        for i in (reversed(range(8)) if inverse else range(8)):
            if inverse:
                left, right = right ^ self._round(left, i), left
            else:
                left, right = right, left ^ self._round(right, i)
        return (left << 5) | right

    def encode_time_bucket(self, value):
        value = business_date(value)
        index = value.year * 12 + value.month - 1 - self.epoch_number
        return base32(self._permute(index), 2)

    def decode_time_bucket(self, value):
        value = normalize_public_id(value)
        if len(value) == 8:
            if not validate_public_id(value):
                raise ValueError("Invalid Public ID")
            value = value[:2]
        if len(value) != 2 or any(c not in ALPHABET for c in value):
            raise ValueError("Invalid month bucket")
        index = self._permute(ALPHABET.index(value[0]) * 32 + ALPHABET.index(value[1]), True)
        year, month = divmod(self.epoch_number + index, 12)
        return f"{year:04d}-{month + 1:02d}"

    def generate_public_id(self, job_date):
        payload = self.encode_time_bucket(job_date) + base32(secrets.randbits(25), 5)
        return payload + checksum(payload)
