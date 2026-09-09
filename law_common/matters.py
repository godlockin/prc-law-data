"""Encrypted, tenant-scoped managed records. No ingestion of executable files."""
from __future__ import annotations
import json
import os
import sqlite3
import time
import uuid
from datetime import date
from contextlib import closing
from pathlib import Path
from cryptography.fernet import Fernet


class MatterStore:
    def __init__(self, path: Path, key: bytes, *, retention_days: int = 30, clock=time.time):
        self.cipher = Fernet(key)
        self.path, self.retention_days, self.clock = path, retention_days, clock
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ValueError("managed database must not be a symlink")
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS matters(id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    payload BLOB NOT NULL, expires REAL NOT NULL, hold INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, matter_id TEXT NOT NULL,
                    owner TEXT NOT NULL, kind TEXT NOT NULL, payload BLOB NOT NULL,
                    status TEXT NOT NULL, assignee TEXT);
            """)
        os.chmod(path, 0o600)

    def connect(self):
        class Connection(sqlite3.Connection):
            def __exit__(self, *args):
                try:
                    return super().__exit__(*args)
                finally:
                    self.close()
        conn = sqlite3.connect(self.path, factory=Connection, timeout=10)
        conn.execute("PRAGMA secure_delete=ON")
        return conn

    def create(self, owner: str, payload: dict, consent: bool) -> dict:
        if consent is not True:
            raise ValueError("explicit storage consent required")
        allowed = {"goal", "summary", "jurisdiction", "event_date"}
        if not isinstance(payload, dict) or set(payload) - allowed or not all(isinstance(v, str) for v in payload.values()):
            raise ValueError("unsupported intake fields")
        raw = json.dumps(payload, ensure_ascii=False).encode()
        if payload.get("event_date"):
            date.fromisoformat(payload["event_date"])
        if len(raw) > 32000 or not payload.get("summary", "").strip():
            raise ValueError("summary required; maximum 32000 bytes")
        identifier, expires = uuid.uuid4().hex, self.clock() + self.retention_days * 86400
        with self.connect() as conn:
            conn.execute("INSERT INTO matters VALUES (?, ?, ?, ?, 0)", (identifier, owner, self.cipher.encrypt(raw), expires))
        return {"id": identifier, "expires_at": expires, "status": "draft_requires_review"}

    def _get(self, conn, owner: str, identifier: str):
        row = conn.execute("SELECT payload,expires,hold FROM matters WHERE id=? AND owner=?", (identifier, owner)).fetchone()
        if not row:
            raise LookupError("record not found")
        if row[1] <= self.clock():
            raise LookupError("record expired; no longer available")
        return row

    def export(self, owner: str, identifier: str) -> dict:
        with self.connect() as conn:
            row = self._get(conn, owner, identifier)
            tickets = conn.execute("SELECT id,kind,payload,status,assignee FROM tickets WHERE matter_id=? AND owner=?", (identifier, owner)).fetchall()
        return {"id": identifier, "payload": json.loads(self.cipher.decrypt(row[0])), "expires_at": row[1],
                "tickets": [{"id": t[0], "kind": t[1], "message": self.cipher.decrypt(t[2]).decode(),
                             "status": t[3], "assignee": t[4]} for t in tickets]}

    def delete(self, owner: str, identifier: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT hold FROM matters WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise LookupError("record not found")
            if row[0]:
                raise ValueError("record is under legal hold; deletion requires authorized review")
            conn.execute("DELETE FROM tickets WHERE matter_id=? AND owner=?", (identifier, owner))
            conn.execute("DELETE FROM matters WHERE id=? AND owner=?", (identifier, owner))
        return {"deleted": True, "scope": "managed_database_only", "external_copies_deleted": False}

    def purge_expired(self) -> int:
        with self.connect() as conn:
            ids = conn.execute("SELECT id FROM matters WHERE expires<=? AND hold=0", (self.clock(),)).fetchall()
            for (identifier,) in ids:
                conn.execute("DELETE FROM tickets WHERE matter_id=?", (identifier,))
                conn.execute("DELETE FROM matters WHERE id=?", (identifier,))
        return len(ids)

    def set_hold(self, identifier: str, enabled: bool, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("legal hold reason required")
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM matters WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise LookupError("record not found")
            payload = json.loads(self.cipher.decrypt(row[0]))
            payload["retention_review"] = {"held": enabled, "reason": reason, "at": self.clock()}
            conn.execute("UPDATE matters SET hold=?,payload=? WHERE id=?", (int(enabled), self.cipher.encrypt(json.dumps(payload).encode()), identifier))

    def ticket(self, owner: str, identifier: str, kind: str, message: str) -> dict:
        if kind not in ("correction", "human_review") or not isinstance(message, str) or not 1 <= len(message) <= 4000:
            raise ValueError("invalid ticket")
        ticket_id = uuid.uuid4().hex
        with self.connect() as conn:
            self._get(conn, owner, identifier)
            conn.execute("INSERT INTO tickets VALUES (?, ?, ?, ?, ?, 'open', NULL)",
                         (ticket_id, identifier, owner, kind, self.cipher.encrypt(message.encode())))
        return {"ticket_id": ticket_id, "status": "open", "message": "已登记，等待人工分配；尚未通知处理人员。"}

    def assign(self, reviewer: str, ticket_id: str) -> dict:
        with self.connect() as conn:
            changed = conn.execute("UPDATE tickets SET status='assigned', assignee=? WHERE id=? AND status='open' "
                                   "AND matter_id IN (SELECT id FROM matters WHERE expires>?)", (reviewer, ticket_id, self.clock())).rowcount
            if not changed:
                raise LookupError("ticket unavailable")
        return {"ticket_id": ticket_id, "status": "assigned", "assignee": reviewer}

    def queue(self) -> list[dict]:
        with self.connect() as conn:
            return [{"id": r[0], "kind": r[1], "status": r[2], "assignee": r[3]} for r in conn.execute(
                "SELECT t.id,t.kind,t.status,t.assignee FROM tickets t JOIN matters m ON m.id=t.matter_id "
                "WHERE m.expires>? AND t.status IN ('open','assigned') ORDER BY t.rowid LIMIT 100", (self.clock(),))]

    def resolve(self, reviewer: str, ticket_id: str, response: str) -> dict:
        if not isinstance(response, str) or not 1 <= len(response) <= 4000:
            raise ValueError("response required")
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM tickets WHERE id=? AND assignee=? AND status='assigned' "
                               "AND matter_id IN (SELECT id FROM matters WHERE expires>?)", (ticket_id, reviewer, self.clock())).fetchone()
            if not row:
                raise LookupError("ticket unavailable")
            text = self.cipher.decrypt(row[0]).decode() + "\n人工处理记录：" + response
            conn.execute("UPDATE tickets SET payload=?,status='resolved' WHERE id=?", (self.cipher.encrypt(text.encode()), ticket_id))
        return {"ticket_id": ticket_id, "status": "resolved", "notified_externally": False}

    def reviewer_view(self, reviewer: str, ticket_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT owner,matter_id FROM tickets WHERE id=? AND assignee=? AND status='assigned'", (ticket_id, reviewer)).fetchone()
            if not row:
                raise LookupError("ticket not assigned to reviewer")
        return self.export(row[0], row[1])
