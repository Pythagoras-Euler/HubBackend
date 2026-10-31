"""Mutable public account identifiers. Numeric uid/userid remain internal.

All namespace mutations lock the singleton row, including denylist edits. This
serializes availability, 72-hour reservations and policy changes across workers.
The caller owns the connection; writes commit only on success.
"""
import calendar
import json
import re
import secrets
import string
import time
from datetime import datetime, timezone

ALPHABET = string.ascii_uppercase + string.digits
HOLD_SECONDS = 72 * 60 * 60
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS account_id_lock (singleton TINYINT PRIMARY KEY) ENGINE=InnoDB",
    "INSERT IGNORE INTO account_id_lock VALUES (1)",
    "CREATE TABLE IF NOT EXISTS account_id (uid INT PRIMARY KEY, public_id VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE, changed_at BIGINT NULL, next_change_at BIGINT NULL, change_credit TINYINT NOT NULL DEFAULT 0) ENGINE=InnoDB",
    "CREATE TABLE IF NOT EXISTS account_id_claim (public_id VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY, owner_uid INT NOT NULL, reserved_until BIGINT NULL) ENGINE=InnoDB",
    "CREATE TABLE IF NOT EXISTS account_id_history (id BIGINT AUTO_INCREMENT PRIMARY KEY, uid INT NOT NULL, public_id VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, started_at BIGINT NULL, ended_at BIGINT NULL, reserved_until BIGINT NULL, source VARCHAR(20) NOT NULL, INDEX(uid,id), INDEX(public_id,id)) ENGINE=InnoDB",
    "CREATE TABLE IF NOT EXISTS account_id_audit (id BIGINT AUTO_INCREMENT PRIMARY KEY, actor_uid INT NOT NULL, target_uid INT NULL, action VARCHAR(32) NOT NULL, detail TEXT NOT NULL, timestamp BIGINT NOT NULL, INDEX(target_uid,id)) ENGINE=InnoDB",
    "CREATE TABLE IF NOT EXISTS account_id_rule (id INT AUTO_INCREMENT PRIMARY KEY, word VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, mode VARCHAR(10) NOT NULL, enabled TINYINT NOT NULL DEFAULT 1, note VARCHAR(256) NOT NULL, UNIQUE(word,mode)) ENGINE=InnoDB",
)


class IDProblem(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def normalize(value):
    # Validate ASCII before uppercasing (e.g. Unicode long-s must not become S).
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{4,6}", value):
        raise IDProblem("invalid_public_id")
    return value.upper()


def rule_word(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{1,6}", value):
        raise IDProblem("invalid_rule")
    return value.upper()


def six_months(timestamp):
    date = datetime.fromtimestamp(timestamp, timezone.utc)
    month = date.month + 6
    year = date.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return int(date.replace(year=year, month=month,
                            day=min(date.day, calendar.monthrange(year, month)[1])).timestamp())


def denied(candidate, rules):
    return any(enabled and (candidate == word if mode == "exact" else word in candidate)
               for _, word, mode, enabled, _ in rules)


class AccountIDs:
    def __init__(self, db, rid, now=None):
        self.db, self.rid = db, rid
        self.now = int(time.time()) if now is None else now
        self.fixed_time = now is not None
        self.locked = False

    async def execute(self, sql, args=None):
        await self.db.execute(self.rid, sql, args)

    async def one(self, sql, args=None):
        await self.execute(sql, args)
        return await self.db.fetchone(self.rid)

    async def all(self, sql, args=None):
        await self.execute(sql, args)
        return await self.db.fetchall(self.rid)

    async def lock(self):
        await self.one("SELECT singleton FROM account_id_lock WHERE singleton=1 FOR UPDATE")
        if not self.fixed_time:
            self.now = int(time.time())
        self.locked = True

    async def audit(self, actor, target, action, detail):
        await self.execute("INSERT INTO account_id_audit(actor_uid,target_uid,action,detail,timestamp) VALUES (%s,%s,%s,%s,%s)",
                           (actor, target, action, json.dumps(detail, ensure_ascii=False), self.now))

    async def rules(self):
        return await self.all("SELECT id,word,mode,enabled,note FROM account_id_rule ORDER BY id" + (" FOR UPDATE" if self.locked else ""))

    async def available(self, value, rules=None):
        value = normalize(value)
        if denied(value, await self.rules() if rules is None else rules):
            return False, "public_id_forbidden"
        claim = await self.one("SELECT reserved_until FROM account_id_claim WHERE public_id=%s" + (" FOR UPDATE" if self.locked else ""), (value,))
        if claim and (claim[0] is None or claim[0] > self.now):
            return False, "public_id_unavailable"
        return True, None

    async def claim(self, value, uid):
        await self.execute("DELETE FROM account_id_claim WHERE public_id=%s AND reserved_until<=%s", (value, self.now))
        await self.execute("INSERT INTO account_id_claim(public_id,owner_uid,reserved_until) VALUES (%s,%s,NULL)", (value, uid))

    async def ensure(self, uid):
        row = await self.one("SELECT public_id FROM account_id WHERE uid=%s", (uid,))
        if row:
            return row[0]
        await self.lock()
        # Locking read avoids a stale repeatable-read snapshot after lock wait.
        row = await self.one("SELECT public_id FROM account_id WHERE uid=%s FOR UPDATE", (uid,))
        if row:
            return row[0]
        if not await self.one("SELECT uid FROM user WHERE uid=%s", (uid,)):
            return None
        rules = await self.rules()
        for _ in range(512):
            value = "".join(secrets.choice(ALPHABET) for _ in range(4))
            if (await self.available(value, rules))[0]:
                await self.claim(value, uid)
                await self.execute("INSERT INTO account_id(uid,public_id) VALUES (%s,%s)", (uid, value))
                await self.execute("INSERT INTO account_id_history(uid,public_id,started_at,source) VALUES (%s,%s,%s,'system')", (uid, value, self.now))
                await self.db.commit(self.rid)
                return value
        raise IDProblem("public_id_generation_exhausted", 503)

    async def status(self, uid):
        await self.ensure(uid)
        row = await self.one("SELECT public_id,changed_at,next_change_at,change_credit FROM account_id WHERE uid=%s", (uid,))
        if not row:
            raise IDProblem("account_not_found", 404)
        return dict(public_id=row[0], changed_at=row[1], next_change_at=row[2],
                    change_credit=bool(row[3]), can_change=bool(row[3]) or row[2] is None or row[2] <= self.now)

    async def change(self, uid, value):
        value = normalize(value)
        await self.ensure(uid)
        await self.lock()
        row = await self.one("SELECT public_id,next_change_at,change_credit FROM account_id WHERE uid=%s FOR UPDATE", (uid,))
        if not row:
            raise IDProblem("account_not_found", 404)
        old, next_at, credit = row
        if value == old:
            raise IDProblem("public_id_unchanged", 409)
        if not credit and next_at is not None and next_at > self.now:
            raise IDProblem("public_id_cooldown", 409)
        ok, error = await self.available(value)
        if not ok:
            raise IDProblem(error, 409)
        await self.claim(value, uid)
        until = self.now + HOLD_SECONDS
        await self.execute("UPDATE account_id_claim SET reserved_until=%s WHERE public_id=%s AND owner_uid=%s", (until, old, uid))
        await self.execute("UPDATE account_id_history SET ended_at=%s,reserved_until=%s WHERE uid=%s AND ended_at IS NULL", (self.now, until, uid))
        await self.execute("INSERT INTO account_id_history(uid,public_id,started_at,source) VALUES (%s,%s,%s,'user')", (uid, value, self.now))
        await self.execute("UPDATE account_id SET public_id=%s,changed_at=%s,next_change_at=%s,change_credit=0 WHERE uid=%s", (value, self.now, six_months(self.now), uid))
        await self.audit(uid, uid, "change", dict(old=old, new=value))
        await self.db.commit(self.rid)
        return await self.status(uid)

    async def grant(self, actor, uid, reason):
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
            raise IDProblem("reason_required")
        if not await self.ensure(uid):
            raise IDProblem("account_not_found", 404)
        await self.lock()
        await self.execute("UPDATE account_id SET change_credit=1 WHERE uid=%s", (uid,))
        await self.audit(actor, uid, "grant_credit", dict(reason=reason.strip()))
        await self.db.commit(self.rid)
        return await self.status(uid)

    async def resolve(self, value, member=False):
        value = normalize(value)
        column = "u.userid" if member else "u.uid"
        row = await self.one(f"SELECT {column} FROM account_id a JOIN user u ON u.uid=a.uid WHERE a.public_id=%s", (value,))
        if not row or (member and (row[0] is None or row[0] < 0)):
            raise IDProblem("account_not_found", 404)
        return row[0]

    async def retire(self, uid, actor=-999):
        """Called before account deletion, inside the caller's transaction.

        Keep private history but release the public name after the same 72 hours.
        The caller commits this together with deletion.
        """
        await self.lock()
        row = await self.one("SELECT public_id FROM account_id WHERE uid=%s FOR UPDATE", (uid,))
        if not row:
            return
        until = self.now + HOLD_SECONDS
        await self.execute("UPDATE account_id_claim SET reserved_until=%s WHERE public_id=%s AND owner_uid=%s", (until, row[0], uid))
        await self.execute("UPDATE account_id_history SET ended_at=%s,reserved_until=%s WHERE uid=%s AND ended_at IS NULL", (self.now, until, uid))
        await self.execute("DELETE FROM account_id WHERE uid=%s", (uid,))
        await self.audit(actor, uid, "account_deleted", dict(public_id=row[0]))

    async def history(self, actor, uid=None, value=None, page=1):
        if page < 1:
            raise IDProblem("invalid_page")
        conditions, args = [], []
        if uid is not None:
            conditions.append("h.uid=%s")
            args.append(uid)
        if value:
            conditions.append("h.public_id=%s")
            # Legacy numeric member IDs are searchable only in this private API.
            args.append(value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value) else normalize(value))
        where = " AND ".join(conditions) or "1=1"
        total = await self.one(f"SELECT COUNT(*) FROM account_id_history h WHERE {where}", tuple(args))
        rows = await self.all(f"SELECT h.id,h.uid,h.public_id,h.started_at,h.ended_at,h.reserved_until,h.source,a.public_id,c.owner_uid,c.reserved_until FROM account_id_history h LEFT JOIN account_id a ON a.uid=h.uid LEFT JOIN account_id_claim c ON c.public_id=h.public_id WHERE {where} ORDER BY h.id DESC LIMIT 50 OFFSET %s", tuple(args + [(page-1)*50]))
        items = [dict(zip(("id", "uid", "public_id", "started_at", "ended_at", "reserved_until", "source", "current_id", "claim_owner_uid", "claim_reserved_until"), row)) for row in rows]
        await self.audit(actor, uid, "view_history", dict(public_id=value, page=page))
        await self.db.commit(self.rid)
        return dict(list=items, total_items=total[0], page=page)

    async def save_rule(self, actor, word, mode, enabled, note, rule_id=None):
        word = rule_word(word)
        if mode not in ("exact", "contains") or not isinstance(enabled, bool) or not isinstance(note, str) or len(note) > 256:
            raise IDProblem("invalid_rule")
        await self.lock()
        duplicate = await self.one("SELECT id FROM account_id_rule WHERE word=%s AND mode=%s FOR UPDATE", (word, mode))
        if duplicate and duplicate[0] != rule_id:
            raise IDProblem("rule_exists", 409)
        if rule_id is None:
            await self.execute("INSERT INTO account_id_rule(word,mode,enabled,note) VALUES (%s,%s,%s,%s)", (word, mode, int(enabled), note))
        else:
            if not await self.one("SELECT id FROM account_id_rule WHERE id=%s", (rule_id,)):
                raise IDProblem("rule_not_found", 404)
            await self.execute("UPDATE account_id_rule SET word=%s,mode=%s,enabled=%s,note=%s WHERE id=%s", (word, mode, int(enabled), note, rule_id))
        await self.audit(actor, None, "save_rule", dict(rule_id=rule_id, word=word, mode=mode, enabled=enabled, note=note))
        await self.db.commit(self.rid)

    async def delete_rule(self, actor, rule_id):
        await self.lock()
        row = await self.one("SELECT word,mode FROM account_id_rule WHERE id=%s", (rule_id,))
        if not row:
            raise IDProblem("rule_not_found", 404)
        await self.execute("DELETE FROM account_id_rule WHERE id=%s", (rule_id,))
        await self.audit(actor, None, "delete_rule", dict(rule_id=rule_id, word=row[0], mode=row[1]))
        await self.db.commit(self.rid)

    async def preview(self, word, mode):
        word = rule_word(word)
        if mode not in ("exact", "contains"):
            raise IDProblem("invalid_rule")
        predicate = "a.public_id=%s" if mode == "exact" else "a.public_id LIKE %s"
        pattern = word if mode == "exact" else f"%{word}%"
        count = await self.one(f"SELECT COUNT(*) FROM account_id a JOIN user u ON u.uid=a.uid WHERE {predicate}", (pattern,))
        rows = await self.all(f"SELECT a.uid,a.public_id,u.name FROM account_id a JOIN user u ON u.uid=a.uid WHERE {predicate} ORDER BY a.uid LIMIT 100", (pattern,))
        return dict(total_items=count[0], list=[dict(uid=r[0], public_id=r[1], name=r[2]) for r in rows])
