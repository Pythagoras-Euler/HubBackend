import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "functions" / "steam_openid.py"
MODULE_SPEC = importlib.util.spec_from_file_location("steam_openid_under_test", MODULE_PATH)
steam_openid = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = steam_openid
MODULE_SPEC.loader.exec_module(steam_openid)

STEAM_OPENID_NAMESPACE = steam_openid.STEAM_OPENID_NAMESPACE
SteamOpenIDError = steam_openid.SteamOpenIDError
claim_steam_openid_nonce = steam_openid.claim_steam_openid_nonce
parse_steam_openid_assertion = steam_openid.parse_steam_openid_assertion
steam_verification_response_is_valid = steam_openid.steam_verification_response_is_valid


CALLBACK_URL = "https://hub.debedidhaulage.cn/auth/steam/callback"
NOW = datetime(2026, 8, 9, 0, 5, tzinfo=timezone.utc)


def valid_params():
    identity = "http://steamcommunity.com/openid/id/76561198000000000"
    return [
        ("openid.ns", STEAM_OPENID_NAMESPACE),
        ("openid.mode", "id_res"),
        ("openid.op_endpoint", "https://steamcommunity.com/openid/login"),
        ("openid.claimed_id", identity),
        ("openid.identity", identity),
        ("openid.return_to", CALLBACK_URL),
        ("openid.response_nonce", "2026-08-09T00:00:00Zunique"),
        ("openid.assoc_handle", "handle"),
        ("openid.signed", "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle"),
        ("openid.sig", "signature"),
    ]


class FakeRedis:
    def __init__(self):
        self.keys = set()

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


class SteamOpenIDTests(unittest.TestCase):
    def test_valid_assertion_is_normalized_for_direct_verification(self):
        assertion = parse_steam_openid_assertion(valid_params(), CALLBACK_URL, now=NOW)

        self.assertEqual(assertion.steamid, 76561198000000000)
        self.assertEqual(assertion.verification_payload["openid.mode"], "check_authentication")

    def test_rejects_assertion_for_another_relying_party(self):
        params = valid_params()
        params[5] = ("openid.return_to", "https://attacker.example/callback")

        with self.assertRaises(SteamOpenIDError):
            parse_steam_openid_assertion(params, CALLBACK_URL, now=NOW)

    def test_rejects_assertion_from_another_openid_provider(self):
        params = valid_params()
        params[2] = ("openid.op_endpoint", "https://attacker.example/openid")

        with self.assertRaises(SteamOpenIDError):
            parse_steam_openid_assertion(params, CALLBACK_URL, now=NOW)

    def test_rejects_stale_nonce(self):
        params = valid_params()
        params[6] = ("openid.response_nonce", "2026-08-08T23:00:00Zstale")

        with self.assertRaises(SteamOpenIDError):
            parse_steam_openid_assertion(params, CALLBACK_URL, now=NOW)

    def test_rejects_duplicate_query_fields(self):
        params = valid_params() + [("openid.identity", valid_params()[4][1])]

        with self.assertRaises(SteamOpenIDError):
            parse_steam_openid_assertion(params, CALLBACK_URL, now=NOW)

    def test_rejects_unsigned_identity(self):
        params = valid_params()
        params[8] = ("openid.signed", "op_endpoint,return_to,response_nonce,assoc_handle")

        with self.assertRaises(SteamOpenIDError):
            parse_steam_openid_assertion(params, CALLBACK_URL, now=NOW)

    def test_provider_response_is_parsed_exactly(self):
        self.assertTrue(steam_verification_response_is_valid(f"ns:{STEAM_OPENID_NAMESPACE}\nis_valid:true\n"))
        self.assertFalse(steam_verification_response_is_valid(f"ns:{STEAM_OPENID_NAMESPACE}\nis_valid:true-but-not-really\n"))

    def test_nonce_can_only_be_claimed_once(self):
        redis_client = FakeRedis()
        claim_steam_openid_nonce(redis_client, "nonce")

        with self.assertRaises(SteamOpenIDError):
            claim_steam_openid_nonce(redis_client, "nonce")


if __name__ == "__main__":
    unittest.main()
