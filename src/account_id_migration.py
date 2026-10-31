"""Offline, resumable public account ID migration. Stop all writers first.

Uses DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME. Defaults to read-only dry run.
Run: python src/account_id_migration.py --apply
Numeric keys and business tables are never renumbered.
"""
import argparse
import asyncio
import os
import time

from account_ids import AccountIDs, SCHEMA


class MigrationDB:
    def __init__(self, connection):
        self.conn, self.cursor = connection, connection.cursor()

    async def execute(self, rid, sql, args=None):
        self.cursor.execute(sql, args)

    async def fetchone(self, rid):
        return self.cursor.fetchone()

    async def fetchall(self, rid):
        return self.cursor.fetchall()

    async def commit(self, rid):
        self.conn.commit()


def migrate(connection, apply=False):
    cursor = connection.cursor()
    cursor.execute("SELECT uid FROM user ORDER BY uid")
    uids = [r[0] for r in cursor.fetchall()]
    if not apply:
        return dict(accounts=len(uids), apply=False)
    for statement in SCHEMA:
        cursor.execute(statement)
    cursor.execute("SHOW COLUMNS FROM user")
    joined = "u.join_timestamp" if "join_timestamp" in {row[0] for row in cursor.fetchall()} else "NULL"
    # Snapshot legacy public numbers before assigning any new IDs. This is
    # restartable even if the process stops between individual account commits.
    cursor.execute(f"INSERT INTO account_id_history(uid,public_id,started_at,ended_at,source) "
                   f"SELECT u.uid,CAST(u.userid AS CHAR),{joined},%s,'legacy' FROM user u "
                   "LEFT JOIN account_id a ON a.uid=u.uid WHERE u.userid>=0 AND a.uid IS NULL "
                   "AND NOT EXISTS (SELECT 1 FROM account_id_history h WHERE h.uid=u.uid AND h.source='legacy')", (int(time.time()),))
    connection.commit()
    service = AccountIDs(MigrationDB(connection), "migration")

    async def backfill():
        for uid in uids:
            await service.ensure(uid)
    asyncio.run(backfill())
    cursor.execute("SELECT COUNT(*) FROM user u LEFT JOIN account_id a ON a.uid=u.uid WHERE a.uid IS NULL")
    if cursor.fetchone()[0]:
        raise RuntimeError("Account ID backfill incomplete")
    return dict(accounts=len(uids), apply=True)


if __name__ == "__main__":
    import pymysql
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("choose --apply or --dry-run")
    connection = pymysql.connect(host=os.environ.get("DB_HOST", "127.0.0.1"),
                                 port=int(os.environ.get("DB_PORT", "3306")),
                                 user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                                 database=os.environ["DB_NAME"], autocommit=False)
    try:
        print(migrate(connection, args.apply))
    finally:
        connection.close()
