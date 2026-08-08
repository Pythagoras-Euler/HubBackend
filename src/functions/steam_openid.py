# Copyright (C) 2022-2026 CharlesWithC All rights reserved.
# Author: @CharlesWithC

"""Strict Steam OpenID 2.0 assertion validation helpers."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re


STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
STEAM_OPENID_ALLOWED_OP_ENDPOINTS = {
    "https://steamcommunity.com/openid",
    "https://steamcommunity.com/openid/",
    STEAM_OPENID_ENDPOINT,
}
STEAM_OPENID_NAMESPACE = "http://specs.openid.net/auth/2.0"
STEAM_IDENTITY_PATTERN = re.compile(r"https?://steamcommunity\.com/openid/id/([0-9]{17})")
STEAM_OPENID_MAX_AGE_SECONDS = 600
STEAM_OPENID_FUTURE_SKEW_SECONDS = 60
STEAM_OPENID_NONCE_TTL_SECONDS = 1200


class SteamOpenIDError(ValueError):
    """Raised when a Steam OpenID assertion is malformed or invalid."""


class SteamOpenIDServiceError(RuntimeError):
    """Raised when Steam or the replay-protection store is unavailable."""


@dataclass(frozen=True)
class SteamOpenIDAssertion:
    steamid: int
    response_nonce: str
    verification_payload: dict[str, str]


def expected_steam_return_to(public_url: str) -> str:
    """Return the exact public callback URL accepted for Steam assertions."""
    public_url = str(public_url).strip().rstrip("/")
    if not public_url.startswith("https://"):
        raise SteamOpenIDError("Steam OpenID requires an HTTPS public URL")
    return public_url + "/auth/steam/callback"


def parse_steam_openid_assertion(query_params, expected_return_to: str, now: datetime | None = None) -> SteamOpenIDAssertion:
    """Validate RP-owned OpenID fields before asking Steam to check the signature."""
    if hasattr(query_params, "multi_items"):
        items = list(query_params.multi_items())
    elif hasattr(query_params, "items"):
        items = list(query_params.items())
    else:
        items = list(query_params)

    params: dict[str, str] = {}
    for key, value in items:
        if not isinstance(key, str) or not isinstance(value, str) or key in params:
            raise SteamOpenIDError("Duplicate or non-string OpenID parameter")
        params[key] = value

    required_values = {
        "openid.ns": STEAM_OPENID_NAMESPACE,
        "openid.mode": "id_res",
        "openid.return_to": expected_return_to,
    }
    for key, expected_value in required_values.items():
        if params.get(key) != expected_value:
            raise SteamOpenIDError(f"Invalid {key}")
    if params.get("openid.op_endpoint") not in STEAM_OPENID_ALLOWED_OP_ENDPOINTS:
        raise SteamOpenIDError("Invalid openid.op_endpoint")

    identity = params.get("openid.identity", "")
    if identity != params.get("openid.claimed_id"):
        raise SteamOpenIDError("Steam claimed_id and identity do not match")
    identity_match = STEAM_IDENTITY_PATTERN.fullmatch(identity)
    if identity_match is None:
        raise SteamOpenIDError("Invalid Steam identity URL")
    steamid = int(identity_match.group(1))
    if steamid <= 0 or steamid >= 2**64:
        raise SteamOpenIDError("Steam ID is outside the uint64 range")

    required_signed_fields = {
        "op_endpoint",
        "claimed_id",
        "identity",
        "return_to",
        "response_nonce",
        "assoc_handle",
    }
    signed_fields = {field.strip() for field in params.get("openid.signed", "").split(",") if field.strip()}
    if not required_signed_fields.issubset(signed_fields) or not params.get("openid.sig"):
        raise SteamOpenIDError("Required OpenID fields are not signed")

    response_nonce = params.get("openid.response_nonce", "")
    try:
        nonce_time = datetime.strptime(response_nonce[:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SteamOpenIDError("Invalid OpenID response nonce") from exc

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    nonce_age = (current_time.astimezone(timezone.utc) - nonce_time).total_seconds()
    if nonce_age > STEAM_OPENID_MAX_AGE_SECONDS or nonce_age < -STEAM_OPENID_FUTURE_SKEW_SECONDS:
        raise SteamOpenIDError("Expired OpenID response nonce")

    verification_payload = dict(params)
    verification_payload["openid.mode"] = "check_authentication"
    return SteamOpenIDAssertion(steamid, response_nonce, verification_payload)


def steam_verification_response_is_valid(response_text: str) -> bool:
    """Parse Steam's key-value direct-verification response exactly."""
    values = {}
    for line in str(response_text).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return values.get("ns") == STEAM_OPENID_NAMESPACE and values.get("is_valid") == "true"


def claim_steam_openid_nonce(redis_client, response_nonce: str) -> None:
    """Atomically reject a Steam assertion nonce that was already consumed."""
    nonce_digest = sha256(response_nonce.encode()).hexdigest()
    try:
        nonce_claimed = redis_client.set(
            f"steam-openid-nonce:{nonce_digest}",
            "1",
            ex=STEAM_OPENID_NONCE_TTL_SECONDS,
            nx=True,
        )
    except Exception as exc:
        raise SteamOpenIDServiceError("Steam OpenID replay protection is unavailable") from exc
    if not nonce_claimed:
        raise SteamOpenIDError("Steam OpenID assertion was already used")


async def verify_steam_openid(app, query_params, public_url: str, dhrid: int) -> int:
    """Verify a Steam assertion and atomically consume its nonce."""
    assertion = parse_steam_openid_assertion(query_params, expected_steam_return_to(public_url))

    try:
        from functions.arequests import arequests

        verification_response = await arequests.post(
            app,
            STEAM_OPENID_ENDPOINT,
            data=assertion.verification_payload,
            dhrid=dhrid,
        )
    except Exception as exc:
        raise SteamOpenIDServiceError("Steam OpenID verification failed") from exc

    if verification_response.status_code // 100 != 2:
        raise SteamOpenIDServiceError("Steam OpenID verification returned an error")
    if not steam_verification_response_is_valid(verification_response.text):
        raise SteamOpenIDError("Steam rejected the OpenID assertion")

    claim_steam_openid_nonce(app.redis, assertion.response_nonce)

    return assertion.steamid
