"""Resolve only supported URL placeholders, preserving custom frontend URLs."""
from urllib.parse import quote


def confirmation_link(template, domain, secret):
    return template.replace('{domain}', domain).replace('{secret}', quote(secret, safe=''))
