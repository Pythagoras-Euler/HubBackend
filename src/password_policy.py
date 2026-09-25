"""Shared policy for new passwords; existing passwords keep their login semantics."""
import re
import unicodedata
import bcrypt


def valid_new_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 30:
        return False
    if any(c.isspace() or unicodedata.category(c).startswith('C') for c in password):
        return False
    if len(password.encode('utf-8')) > 72:
        return False
    return all(re.search(pattern, password) for pattern in (r'[a-z]', r'[A-Z]', r'[0-9]', r'[!@#$%^&*]'))


def verify_password(password_bytes, stored_hash):
    # bcrypt 5 rejects overlong input; treat it as a failed login, never truncate.
    if len(password_bytes) > 72:
        return False
    try:
        return bcrypt.checkpw(password_bytes, stored_hash)
    except ValueError:
        return False
