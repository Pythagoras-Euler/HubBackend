"""Database boundary for Public IDs; all writes use the caller's transaction."""
import logging
import time

from pymysql.err import IntegrityError

from public_ids import MAX_ATTEMPTS, normalize_public_id, validate_public_id


def is_public_id_collision(exc):
    return (isinstance(exc, IntegrityError) and exc.args[0] == 1062
            and "uq_dlog_public_id" in str(exc))


async def insert_delivery(app, rid, values, job_date):
    """Generate before INSERT so NOT NULL holds, without allocating another PK."""
    values = dict(values)
    values["imported_at"] = int(time.time())
    columns = list(values) + ["public_id"]
    sql = (f"INSERT INTO dlog ({','.join(columns)}) VALUES "
           f"({','.join(['%s'] * len(columns))})")
    for _ in range(MAX_ATTEMPTS):
        public_id = app.public_ids.generate_public_id(job_date)
        await app.db.execute(rid, "SAVEPOINT public_id_insert")
        try:
            await app.db.execute(rid, "INSERT INTO public_id_registry VALUES (%s)", (public_id,))
            await app.db.execute(rid, sql, tuple(values.values()) + (public_id,))
            await app.db.execute(rid, "RELEASE SAVEPOINT public_id_insert")
            return public_id
        except Exception as exc:
            await app.db.execute(rid, "ROLLBACK TO SAVEPOINT public_id_insert")
            await app.db.execute(rid, "RELEASE SAVEPOINT public_id_insert")
            if not is_public_id_collision(exc):
                raise
    logging.getLogger(__name__).error("Public ID collision retry limit reached")
    raise RuntimeError("Public ID collision retry limit reached")


async def resolve_public_id(app, rid, public_id):
    if not validate_public_id(public_id):
        raise ValueError("Invalid Public ID checksum or format")
    await app.db.execute(rid, "SELECT logid FROM dlog WHERE public_id=%s AND logid>=0",
                         (normalize_public_id(public_id),))
    row = await app.db.fetchone(rid)
    return row[0] if row else None
