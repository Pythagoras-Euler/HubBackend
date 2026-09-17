import json
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pymysql.err import IntegrityError
from public_ids import ALPHABET, MAX_ATTEMPTS, PublicIDs, base32, business_date, validate_public_id
from public_id_store import insert_delivery, resolve_public_id
from public_id_migration import backfill, stored_job_date
from delivery_filters import metadata_filters

CODEC = PublicIDs("2020-01", b"public-unit-test-key-not-a-production-secret")


class CodecTests(unittest.TestCase):
    def test_all_1024_months_are_bijective_and_reversible(self):
        buckets = set()
        for i in range(1024):
            year, month = divmod(CODEC.epoch_number + i, 12)
            value = date(year, month + 1, 1)
            bucket = CODEC.encode_time_bucket(value)
            buckets.add(bucket)
            self.assertEqual(CODEC.decode_time_bucket(bucket), value.strftime("%Y-%m"))
        self.assertEqual(len(buckets), 1024)
        self.assertNotEqual(sorted(buckets), [CODEC.encode_time_bucket(date(2020 + i // 12, i % 12 + 1, 1)) for i in range(1024)])

    def test_bounds_and_utc_month(self):
        for invalid in ("2019-12-31", "2105-05-01"):
            with self.assertRaisesRegex(ValueError, "1024-month"):
                CODEC.encode_time_bucket(invalid)
        for valid in ("2020-01-01", "2105-04-30"):
            self.assertEqual(CODEC.decode_time_bucket(CODEC.encode_time_bucket(valid)), valid[:7])
        self.assertEqual(CODEC.encode_time_bucket("2026-10-01T00:30:00+01:00"), CODEC.encode_time_bucket("2026-09-01"))

    def test_every_single_character_substitution_is_detected(self):
        pid = CODEC.generate_public_id("2025-09-10")
        for pos in range(8):
            for char in ALPHABET:
                if char != pid[pos]:
                    self.assertFalse(validate_public_id(pid[:pos] + char + pid[pos + 1:]))

    def test_format_case_and_invalid_input(self):
        pid = CODEC.generate_public_id("2026-09-01")
        self.assertEqual(len(pid), 8)
        self.assertTrue(set(pid) <= set(ALPHABET))
        self.assertTrue(validate_public_id(" " + pid.lower() + " "))
        for value in (None, 123, pid[:-1], pid + "A", "IIIIIIII", "LLLLLLLL", "OOOOOOOO", "UUUUUUUU", "--------", "ſ2345678", "１２３４５６７８"):
            self.assertFalse(validate_public_id(value))
        self.assertEqual(CODEC.decode_time_bucket(pid.lower()), "2026-09")

    def test_body_does_not_encode_internal_sequence(self):
        # Controlled entropy proves the output uses the CSPRNG rather than a PK.
        with patch("public_ids.secrets.randbits", return_value=123456):
            for internal_id in range(1000, 1101):
                body = CODEC.generate_public_id("2026-09-01")[2:7]
                self.assertEqual(body, base32(123456, 5))
                self.assertNotEqual(body, base32(internal_id, 5))
                self.assertNotEqual(body, str(internal_id).zfill(5))

    def test_configuration_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                PublicIDs.from_env()
        with self.assertRaises(ValueError):
            PublicIDs("2020-01", b"short")
        self.assertNotEqual(CODEC.fingerprint, PublicIDs("2020-02", CODEC.key).fingerprint)
        for value in (None, 0, "invalid", True):
            with self.assertRaises(ValueError):
                business_date(value)


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_collision_retries_with_new_entropy_inside_transaction(self):
        first, second = [CODEC.generate_public_id("2025-09-01") for _ in range(2)]
        db = SimpleNamespace(execute=AsyncMock(), fetchone=AsyncMock())
        seen = set([first])

        async def execute(rid, sql, args=None):
            if sql.startswith("INSERT INTO public_id_registry"):
                if args[0] in seen:
                    raise IntegrityError(1062, "Duplicate entry for key uq_dlog_public_id")
                seen.add(args[0])

        db.execute.side_effect = execute
        app = SimpleNamespace(db=db, public_ids=SimpleNamespace(generate_public_id=lambda _: next(candidates)))
        candidates = iter([first, second])
        self.assertEqual(await insert_delivery(app, "r", {"userid": 4}, "2025-09-01"), second)
        statements = [call.args[1] for call in db.execute.await_args_list]
        self.assertIn("ROLLBACK TO SAVEPOINT public_id_insert", statements)
        self.assertEqual(sum(s.startswith("INSERT INTO dlog ") for s in statements), 1)
        self.assertNotIn("COMMIT", statements)

    async def test_unrelated_duplicate_and_exhaustion(self):
        for key, expected in (("PRIMARY", IntegrityError), ("uq_dlog_public_id", RuntimeError)):
            attempts = []

            async def execute(rid, sql, args=None):
                if sql.startswith("INSERT"):
                    attempts.append(sql)
                    raise IntegrityError(1062, f"Duplicate entry for key {key}")

            app = SimpleNamespace(db=SimpleNamespace(execute=AsyncMock(side_effect=execute)), public_ids=CODEC)
            with self.assertRaises(expected):
                await insert_delivery(app, "r", {}, "2025-09-01")
            self.assertEqual(len(attempts), 1 if key == "PRIMARY" else MAX_ATTEMPTS)

    async def test_lookup_validates_before_query_and_normalizes(self):
        db = SimpleNamespace(execute=AsyncMock(), fetchone=AsyncMock(return_value=(123,)))
        app = SimpleNamespace(db=db)
        with self.assertRaises(ValueError):
            await resolve_public_id(app, "r", "BAD")
        db.execute.assert_not_awaited()
        pid = CODEC.generate_public_id("2025-09-01")
        self.assertEqual(await resolve_public_id(app, "r", pid.lower()), 123)
        self.assertEqual(db.execute.call_args.args[-1], (pid,))
        db.fetchone.return_value = None
        self.assertIsNone(await resolve_public_id(app, "r", pid))


class SQLiteMigrationConnection:
    """Execute backfill DML against real constraints; only adapt MySQL metadata/DDL.

    This does not claim to replace the optional MariaDB integration tests.
    """
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.not_null = set()
        for table in ("dlog", "dlog_deleted"):
            self.db.execute(f"CREATE TABLE {table}(logid INTEGER PRIMARY KEY, timestamp INTEGER, data TEXT, public_id TEXT UNIQUE, trackerid INTEGER, imported_at INTEGER)")
        self.db.execute("CREATE TABLE public_id_registry(public_id TEXT UNIQUE)")

    def cursor(self):
        parent = self

        class Cursor:
            rowcount = 0

            def execute(self, sql, args=()):
                self.rows = []
                if sql.startswith("SHOW COLUMNS"):
                    self.rows = [("public_id",)]
                elif sql.startswith("ALTER TABLE"):
                    parent.not_null.add(sql.split()[2])
                else:
                    try:
                        result = parent.db.execute(sql.replace("%s", "?"), args)
                        self.rows = result.fetchall()
                        self.rowcount = result.rowcount
                    except sqlite3.IntegrityError as exc:
                        raise IntegrityError(1062, "Duplicate entry for key uq_dlog_public_id") from exc

            def fetchall(self):
                return self.rows

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.db.commit()


class MigrationTests(unittest.TestCase):
    def test_force_renumbers_existing_but_preview_and_default_preserve_ids(self):
        conn = SQLiteMigrationConnection()
        self.addCleanup(conn.db.close)
        raw = json.dumps({"data": {"object": {"stop_time": "2025-09-15"}}})
        old = CODEC.generate_public_id("2026-09-01")
        conn.db.execute("INSERT INTO dlog VALUES(1,1900000000,?,?,55,1900000000)", (raw, old))
        conn.db.execute("INSERT INTO public_id_registry VALUES(?)", (old,))
        with patch("public_id_migration.prepare"):
            preview = backfill(conn, CODEC, False, force=True)
            self.assertEqual(preview["generated"], 1)
            self.assertEqual(conn.db.execute("SELECT public_id FROM dlog").fetchone()[0], old)
            result = backfill(conn, CODEC, True, force=True)
            self.assertEqual((result["existing"], result["generated"]), (1, 1))
            new, legacy, imported = conn.db.execute("SELECT public_id,trackerid,imported_at FROM dlog").fetchone()
            self.assertNotEqual(new, old)
            self.assertEqual(CODEC.decode_time_bucket(new), "2025-09")
            self.assertEqual((legacy, imported), (55, 1900000000))
            backfill(conn, CODEC, True)
            self.assertEqual(conn.db.execute("SELECT public_id FROM dlog").fetchone()[0], new)

    def test_backfill_rerun_legacy_dates_archives_and_missing_date(self):
        conn = SQLiteMigrationConnection()
        self.addCleanup(conn.db.close)
        raw = json.dumps({"data": {"object": {"stop_time": "2025-09-15"}}})
        conn.db.execute("INSERT INTO dlog VALUES(1,1900000000,?,NULL,48372910,1900000000)", (raw,))
        conn.db.execute("INSERT INTO dlog VALUES(2,1900000000,'{}',NULL,999,1900000000)")
        conn.db.execute("INSERT INTO dlog_deleted VALUES(3,1900000000,?,NULL,888,1900000000)", (raw,))
        warnings = []
        with patch("public_id_migration.prepare"):
            preview = backfill(conn, CODEC, False, batch_size=1, warn=warnings.append)
            self.assertEqual(preview["generated"], 2)
            self.assertEqual(conn.db.execute("SELECT COUNT(public_id) FROM dlog").fetchone()[0], 0)
            first = backfill(conn, CODEC, True, batch_size=1, warn=warnings.append)
            self.assertEqual((first["generated"], first["missing_date"]), (2, 1))
            saved = conn.db.execute("SELECT public_id,trackerid,imported_at FROM dlog WHERE logid=1").fetchone()
            self.assertEqual(CODEC.decode_time_bucket(saved[0]), "2025-09")
            self.assertEqual(saved[1:], (48372910, 1900000000))
            self.assertFalse(conn.not_null)
            changed = json.dumps({"data": {"object": {"stop_time": "2026-09-15"}}})
            conn.db.execute("UPDATE dlog SET data=? WHERE logid=1", (changed,))
            second = backfill(conn, CODEC, True, batch_size=1, warn=warnings.append)
            self.assertEqual((second["generated"], second["existing"]), (0, 2))
            self.assertEqual(saved, conn.db.execute("SELECT public_id,trackerid,imported_at FROM dlog WHERE logid=1").fetchone())
            conn.db.execute("UPDATE dlog SET data=? WHERE logid=2", (raw,))
            backfill(conn, CODEC, True, batch_size=1, warn=warnings.append)
            self.assertEqual(conn.not_null, {"dlog", "dlog_deleted"})

    def test_backfill_collision_and_date_only(self):
        conn = SQLiteMigrationConnection()
        self.addCleanup(conn.db.close)
        raw = json.dumps({"data": {"object": {"stop_time": "2025-09-15"}}})
        conn.db.execute("INSERT INTO dlog VALUES(1,1900000000,?,NULL,55,NULL)", (raw,))
        pid = CODEC.generate_public_id("2025-09-15")
        alternate = CODEC.generate_public_id("2025-09-15")
        conn.db.execute("INSERT INTO public_id_registry VALUES(?)", (pid,))
        with patch("public_id_migration.prepare"), patch.object(PublicIDs, "generate_public_id", side_effect=[pid, alternate]):
            result = backfill(conn, CODEC, True)
        self.assertEqual(result["collisions"], 1)
        self.assertEqual(conn.db.execute("SELECT public_id FROM dlog").fetchone()[0], alternate)
        self.assertEqual(stored_job_date(-1, datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp(), ""), date(2025, 9, 1))

    def test_source_timestamp_never_falls_back_to_import_time(self):
        with self.assertRaises(KeyError):
            stored_job_date(1, 1900000000, '{"data":{"object":{}}}')


class FilterTests(unittest.TestCase):
    def test_sql_is_parameterized_literal_substring_and_combines_filters(self):
        sql, args = metadata_filters("London' OR 1=1 --", "Paris%_", "Steel")
        self.assertNotIn("London", sql)
        self.assertIn("EXISTS", sql)
        self.assertEqual(args, ("London' OR 1=1 --", "London' OR 1=1 --", "Paris%_", "Paris%_", "Steel"))
        self.assertEqual(metadata_filters(" ", None, ""), ("", ()))
        with self.assertRaises(ValueError):
            metadata_filters(cargo="x" * 129)


if __name__ == "__main__":
    unittest.main()
