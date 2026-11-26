"""SQLite-хранилище: пользователи (токены GSI) и сыгранные матчи."""
from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from core.models import MatchRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    token      TEXT    NOT NULL UNIQUE,
    created_at REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id      INTEGER NOT NULL,
    map_name   TEXT,
    source     TEXT,
    created_at REAL    NOT NULL,
    data       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_user ON matches(tg_id, id DESC);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:  # commit / rollback
                yield c
        finally:
            c.close()

    async def _run(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    # ---------------------------------------------------------------- init
    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    async def init(self) -> None:
        await self._run(self._init)

    # --------------------------------------------------------------- users
    def _get_or_create_token(self, tg_id: int) -> str:
        with self._conn() as c:
            row = c.execute("SELECT token FROM users WHERE tg_id=?", (tg_id,)).fetchone()
            if row:
                return row["token"]
            token = secrets.token_urlsafe(24)
            c.execute("INSERT INTO users(tg_id, token, created_at) VALUES (?,?,?)",
                      (tg_id, token, time.time()))
            return token

    async def get_or_create_token(self, tg_id: int) -> str:
        return await self._run(self._get_or_create_token, tg_id)

    def _regen_token(self, tg_id: int) -> str:
        token = secrets.token_urlsafe(24)
        with self._conn() as c:
            c.execute(
                "INSERT INTO users(tg_id, token, created_at) VALUES (?,?,?) "
                "ON CONFLICT(tg_id) DO UPDATE SET token=excluded.token",
                (tg_id, token, time.time()),
            )
        return token

    async def regen_token(self, tg_id: int) -> str:
        return await self._run(self._regen_token, tg_id)

    def _user_by_token(self, token: str) -> int | None:
        with self._conn() as c:
            row = c.execute("SELECT tg_id FROM users WHERE token=?", (token,)).fetchone()
            return int(row["tg_id"]) if row else None

    async def user_by_token(self, token: str) -> int | None:
        return await self._run(self._user_by_token, token)

    # ------------------------------------------------------------- matches
    def _save_match(self, tg_id: int, m: MatchRecord) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO matches(tg_id, map_name, source, created_at, data) VALUES (?,?,?,?,?)",
                (tg_id, m.map_name, m.source, time.time(), json.dumps(m.to_dict(), ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    async def save_match(self, tg_id: int, m: MatchRecord) -> int:
        return await self._run(self._save_match, tg_id, m)

    def _get_match(self, tg_id: int, match_id: int | None) -> tuple[int, MatchRecord] | None:
        with self._conn() as c:
            if match_id is None:
                row = c.execute(
                    "SELECT id, data FROM matches WHERE tg_id=? ORDER BY id DESC LIMIT 1", (tg_id,)
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT id, data FROM matches WHERE tg_id=? AND id=?", (tg_id, match_id)
                ).fetchone()
        if not row:
            return None
        return int(row["id"]), MatchRecord.from_dict(json.loads(row["data"]))

    async def get_match(self, tg_id: int, match_id: int | None = None):
        return await self._run(self._get_match, tg_id, match_id)

    def _list_matches(self, tg_id: int, limit: int) -> list[tuple[int, MatchRecord]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, data FROM matches WHERE tg_id=? ORDER BY id DESC LIMIT ?", (tg_id, limit)
            ).fetchall()
        return [(int(r["id"]), MatchRecord.from_dict(json.loads(r["data"]))) for r in rows]

    async def list_matches(self, tg_id: int, limit: int = 10):
        return await self._run(self._list_matches, tg_id, limit)
