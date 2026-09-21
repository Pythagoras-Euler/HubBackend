"""Additive tracker import schema; no historical deliveries are rewritten."""
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS active_delivery (
        provider VARCHAR(32) NOT NULL, sourceid BIGINT NOT NULL, steamid VARCHAR(20) NOT NULL,
        status VARCHAR(24) NOT NULL, payload MEDIUMTEXT NOT NULL,
        first_seen BIGINT NOT NULL, updated_at BIGINT NOT NULL, closed_at BIGINT NULL,
        closed_by INT NULL, matched_logid INT NULL,
        PRIMARY KEY(provider,sourceid), KEY(steamid,status)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS delivery_source (
        provider VARCHAR(32) NOT NULL, sourceid BIGINT NOT NULL, logid INT NULL,
        state VARCHAR(24) NOT NULL DEFAULT 'pending', payload MEDIUMTEXT NULL,
        updated_at BIGINT NOT NULL, route_retry_at BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(provider,sourceid), KEY(logid)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS tracker_inbox (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, provider VARCHAR(32) NOT NULL,
        digest CHAR(64) NOT NULL, payload MEDIUMTEXT NOT NULL,
        received_at BIGINT NOT NULL, processed_at BIGINT NULL,
        UNIQUE KEY uq_tracker_receipt(provider,digest)
    ) ENGINE=InnoDB""",
)

def prepare(cursor):
    for sql in STATEMENTS:
        cursor.execute(sql)
