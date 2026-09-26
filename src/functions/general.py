# Copyright (C) 2022-2026 CharlesWithC All rights reserved.
# Author: @CharlesWithC

import json
import os
import random
import re
import string
import time
from datetime import datetime, timezone

import requests
from fastapi import Request

import multilang as ml
from functions.dataop import *
from static import *
from ip_location import country_for_ip, country_changed, local_ip, request_country, location_for_ip


class Dict2Obj(object):
    def __init__(self, d):
        for key in d:
            if type(d[key]) is dict:
                data = Dict2Obj(d[key])
                setattr(self, key, data)
            else:
                setattr(self, key, d[key])

class RateLimitException(Exception):
    pass

def test_security_bypass_enabled():
    """Return whether test-only CAPTCHA/MFA security challenges are bypassed."""
    enabled_values = {"1", "true", "yes", "on"}
    return (
        os.environ.get("HUB_TEST_BYPASS_SECURITY_CHECKS", "false").strip().lower() in enabled_values
        or os.environ.get("HUB_TEST_PASSWORD_ONLY_AUTH", "false").strip().lower() in enabled_values
    )


def test_password_only_auth_enabled():
    """Deprecated compatibility alias for older deployment environments."""
    return test_security_bypass_enabled()

def restart(app):
    time.sleep(3)
    os.system(f"nohup ./launcher hub restart {app.config.abbr} > /dev/null")

def genrid():
    return str(int(time.time()*10000000)) + str(random.randint(0, 10000)).zfill(5)

def gensecret(length = 32):
    return ''.join(random.choice(string.ascii_letters) for i in range(length))

def getDayStartTs(timestamp):
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())

def isurl(s): # s could be NoneType
    try:
        r = re.compile(
                r'^(?:http)s?://' # http:// or https://
                r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' #domain...
                r'localhost|' #localhost...
                r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
                r'(?::\d+)?' # optional port
                r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        return re.match(r, s) is not None
    except:
        return False

def validateUrl(s):
    if not isurl(s):
        return ""
    else:
        return s

def getDomainFromUrl(s):
    if not isurl(s):
        return False
    try:
        r = re.search(r"(?<=://)[^/]+", s)
        if r:
            return r.group(0)
        else:
            return False
    except:
        return False

def getFullCountry(abbr):
    if not abbr or abbr.upper() == "XX":
        return "-*-*-"
    if abbr.upper() in ISO_COUNTRIES.keys():
        return convertQuotation(ISO_COUNTRIES[abbr.upper()])
    else:
        return ""

def is_local_ip(ip):
    return local_ip(ip)

def getRequestCountry(request, abbr = False):
    country = request_country(request)
    if abbr:
        return country
    language = request.headers.get('accept-language', 'en').split(',')[0]
    return convertQuotation(location_for_ip(request.client.host if request.client else '', country, language))

def getUserAgent(request):
    if "user-agent" in request.headers.keys():
        if len(request.headers["user-agent"]) < 256:
            return convertQuotation(request.headers["user-agent"])
        else:
            return convertQuotation(request.headers["user-agent"])[:256]
    else:
        return ""

def DisableDiscordIntegration(app):
    request = Request(scope={"type":"http", "app": app, "headers": []})
    app.config.discord_bot_token = ""
    try:
        if app.config.hook_audit_log.webhook_url != "":
            requests.post(app.config.hook_audit_log.webhook_url, data=json.dumps({"embeds": [{"title": ml.ctr(request, "attention_required"), "description": ml.ctr(request, "invalid_discord_token"), "color": int(app.config.hex_color, 16), "footer": {"text": "System"}, "timestamp": datetime.now(timezone.utc).isoformat()}]}), headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    except:
        pass

async def EnsureEconomyBalance(request, userid):
    (app, dhrid) = (request.app, request.state.dhrid)
    await app.db.execute(dhrid, f"SELECT balance FROM economy_balance WHERE userid = {userid}")
    t = await app.db.fetchall(dhrid)
    if len(t) == 0:
        await app.db.execute(dhrid, f"INSERT INTO economy_balance VALUES ({userid}, 0)")

def configured_trackers(app):
    ret = []
    for tracker in app.config.trackers:
        ret.append(tracker["type"])
    return ret
