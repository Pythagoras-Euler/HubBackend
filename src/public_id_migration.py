"""Offline, restartable Public ID migration. Run with all writers stopped.

python src/public_id_migration.py --dry-run
python src/public_id_migration.py --apply
Connection uses DB_HOST/PORT/USER/PASSWORD/NAME, never prints credentials.
"""
import argparse
import base64
import json
import os
import zlib

from public_ids import MAX_ATTEMPTS, PublicIDs, business_date, normalize_public_id, validate_public_id

TABLES = ("dlog", "dlog_deleted")


def stored_job_date(logid, timestamp, raw):
    # Negative IDs are adjustments performed at their stored timestamp.
    if logid < 0:
        return business_date(timestamp)
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        import zstandard
        packed = base64.b64decode(raw)
        try:
            unpacked = zstandard.ZstdDecompressor().decompress(packed)
        except zstandard.ZstdError:
            unpacked = zlib.decompress(packed)
        obj = json.loads(unpacked)
    # The persisted tracker payload is authoritative, even for older Trucky
    # rows whose timestamp was the import time. No receipt-time fallback.
    return business_date(obj["data"]["object"]["stop_time"])


def columns(cur, table):
    cur.execute(f"SHOW COLUMNS FROM {table}")
    return {row[0]: row for row in cur.fetchall()}


def prepare(cur, codec):
    # DDL commits implicitly on MariaDB. Every step is intentionally resumable.
    for table in TABLES:
        cur.execute("SELECT ENGINE FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        row = cur.fetchone()
        if not row or row[0].upper() != "INNODB":
            raise ValueError(f"{table} must exist and use InnoDB before migration")
        cur.execute(f"SELECT logid FROM {table} GROUP BY logid HAVING COUNT(*)>1 LIMIT 1")
        if cur.fetchone():
            raise ValueError(f"Resolve duplicate {table}.logid values before migration")
    cur.execute("CREATE TABLE IF NOT EXISTS public_id_config "
                "(singleton TINYINT PRIMARY KEY, fingerprint CHAR(64) NOT NULL) ENGINE=InnoDB")
    cur.execute("SELECT fingerprint FROM public_id_config WHERE singleton=1")
    row = cur.fetchone()
    if row and row[0] != codec.fingerprint:
        raise ValueError("Public ID key/epoch differs from the persisted configuration")
    if not row:
        cur.execute("INSERT INTO public_id_config VALUES (1,%s)", (codec.fingerprint,))
    # A reservation survives deletion, so old links can never be reassigned.
    cur.execute("CREATE TABLE IF NOT EXISTS public_id_registry "
                "(public_id CHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, "
                "UNIQUE KEY uq_dlog_public_id (public_id)) ENGINE=InnoDB")
    for table in TABLES:
        existing = columns(cur, table)
        if "public_id" not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN public_id "
                        "CHAR(8) CHARACTER SET ascii COLLATE ascii_bin NULL")
        if "imported_at" not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN imported_at BIGINT NULL")
        cur.execute(f"SHOW INDEX FROM {table} WHERE Key_name='uq_dlog_public_id'")
        if not cur.fetchone():
            cur.execute(f"CREATE UNIQUE INDEX uq_dlog_public_id ON {table}(public_id)")
        cur.execute(f"INSERT IGNORE INTO public_id_registry SELECT public_id FROM {table} "
                    "WHERE public_id IS NOT NULL")


def backfill(conn, codec=None, apply=False, batch_size=500, warn=print):
    from public_id_store import is_public_id_collision
    stats = dict(total=0, existing=0, generated=0, missing_date=0, collisions=0, errors=0)
    earliest = latest = None
    cur = conn.cursor()
    if apply:
        if codec is None:
            raise ValueError("Configure key and epoch before applying")
        prepare(cur, codec)
        conn.commit()
    for table in TABLES:
        has_public = "public_id" in columns(cur, table)
        last = -2147483649
        while True:
            cur.execute(f"SELECT logid,timestamp,data,{'public_id' if has_public else 'NULL'} "
                        f"FROM {table} WHERE logid>%s ORDER BY logid LIMIT %s", (last, batch_size))
            rows = cur.fetchall()
            if not rows:
                break
            for logid, timestamp, raw, public_id in rows:
                last = logid
                stats["total"] += 1
                if public_id is not None:
                    stats["existing"] += 1
                    if not validate_public_id(public_id) or public_id != normalize_public_id(public_id):
                        stats["errors"] += 1
                        warn(json.dumps(dict(table=table, logid=logid, warning="Invalid existing Public ID")))
                    continue
                try:
                    day = stored_job_date(logid, timestamp, raw)
                except (ValueError, TypeError, KeyError, OverflowError, zlib.error):
                    stats["missing_date"] += 1
                    warn(json.dumps(dict(table=table, logid=logid, warning="Missing/invalid business date")))
                    continue
                earliest = min(earliest, day) if earliest else day
                latest = max(latest, day) if latest else day
                try:
                    if codec:
                        codec.encode_time_bucket(day)
                    if apply:
                        for attempt in range(MAX_ATTEMPTS):
                            candidate = codec.generate_public_id(day)
                            cur.execute("SAVEPOINT public_id_row")
                            try:
                                cur.execute("INSERT INTO public_id_registry VALUES (%s)", (candidate,))
                                cur.execute(f"UPDATE {table} SET public_id=%s "
                                            "WHERE logid=%s AND public_id IS NULL", (candidate, logid))
                                cur.execute("RELEASE SAVEPOINT public_id_row")
                                break
                            except Exception as exc:
                                cur.execute("ROLLBACK TO SAVEPOINT public_id_row")
                                cur.execute("RELEASE SAVEPOINT public_id_row")
                                if not is_public_id_collision(exc):
                                    raise
                                stats["collisions"] += 1
                        else:
                            raise RuntimeError("Public ID collision retry limit reached")
                    stats["generated"] += 1
                except Exception as exc:
                    stats["errors"] += 1
                    warn(json.dumps(dict(table=table, logid=logid, warning=str(exc))))
            if apply:
                conn.commit()
    stats["earliest_unassigned_date"] = str(earliest) if earliest else None
    stats["latest_unassigned_date"] = str(latest) if latest else None
    stats["suggested_epoch"] = f"{max(1, earliest.year - 5):04d}-01" if earliest else None
    if apply and not stats["missing_date"] and not stats["errors"]:
        for table in TABLES:
            cur.execute(f"ALTER TABLE {table} MODIFY public_id "
                        "CHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL")
        conn.commit()
    cur.close()
    return stats


def main():
    import pymysql
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    codec = PublicIDs.from_env() if args.apply or os.environ.get("VTCHUB_PUBLIC_ID_EPOCH") else None
    conn = pymysql.connect(host=os.environ.get("DB_HOST", "localhost"),
                           port=int(os.environ.get("DB_PORT", "3306")),
                           user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                           database=os.environ["DB_NAME"], autocommit=False)
    try:
        result = backfill(conn, codec, args.apply, args.batch_size)
        print(json.dumps(result, indent=2))
        return 1 if result["errors"] or result["missing_date"] else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
