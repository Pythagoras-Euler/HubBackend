"""Offline IP country/province lookup using DB-IP Lite (CC BY 4.0)."""
import ipaddress
import logging
import os
from functools import lru_cache

import maxminddb
import pycountry

COUNTRIES = {country.alpha_2: country.name for country in pycountry.countries}


def parse_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address
    except ValueError:
        return None


def local_ip(value):
    address = parse_ip(value)
    return bool(address and (address.is_private or address.is_loopback or address.is_link_local))


@lru_cache(maxsize=1)
def database():
    path = os.environ.get('GEOIP_DATABASE', '/app/geoip/city.mmdb')
    try:
        return maxminddb.open_database(path)
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        logging.getLogger(__name__).warning('IP location database unavailable: %s', path)
        return None


@lru_cache(maxsize=8192)
def lookup(value):
    address = parse_ip(value)
    if address is None or not address.is_global:
        return {}
    reader = database()
    if reader is None:
        return {}
    try:
        return reader.get(str(address)) or {}
    except (ValueError, maxminddb.InvalidDatabaseError):
        return {}


def country_for_ip(value):
    if local_ip(value):
        return '00'
    code = lookup(value).get('country', {}).get('iso_code', '').upper()
    return code if code in COUNTRIES else 'XX'


def request_country(request):
    # Only enable for a trusted proxy that strips incoming client headers.
    if os.environ.get('TRUST_CF_IPCOUNTRY', '').lower() == 'true':
        code = request.headers.get('cf-ipcountry', '').strip().upper()
        if code in COUNTRIES or code == 'T1':
            return code
    return country_for_ip(request.client.host) if request.client else 'XX'


def location_for_ip(value, country=None, language='en'):
    data = lookup(value)
    code = country if country and country != 'XX' else country_for_ip(value)
    if code == '00':
        return '本地网络' if language.startswith('zh') else 'Local Network'
    if code == 'T1':
        return 'Tor'
    if code not in COUNTRIES:
        return '-*-*-'
    lang = 'zh-CN' if language.startswith('zh') else language.split('-')[0]
    # Avoid combining a proxy country with a province from a different country.
    same_country = data.get('country', {}).get('iso_code') == code
    names = data.get('country', {}).get('names', {}) if same_country else {}
    name = names.get(lang) or names.get('en') or COUNTRIES[code]
    subdivisions = data.get('subdivisions') or []
    province = subdivisions[0].get('names', {}) if same_country and subdivisions else {}
    region = province.get(lang) or province.get('en')
    return f'{name} / {region}' if region else name


def country_changed(previous, current, previous_ip):
    if previous in ('', None, 'XX'):
        previous = country_for_ip(previous_ip)
    return previous not in ('', None, 'XX') and current != 'XX' and previous != current
