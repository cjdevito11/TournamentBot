# repositories/base_repo.py
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

import aiomysql

from db.pool import DbPool
from db.tx import get_cursor, transaction


def to_json(v: Any) -> str | None:
    if v is None:
        return None
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


class BaseRepo:
    """
    Base repository with small helpers to keep concrete repos readable.
    Repos should not contain business rules or Discord logic.
    """

    def __init__(self, db: DbPool) -> None:
        self._db = db

    @property
    def pool(self) -> aiomysql.Pool:
        return self._db.pool

    async def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> Mapping[str, Any] | None:
        async with get_cursor(self.pool, dict_rows=True) as cur:
            await cur.execute(sql, params or ())
            return await cur.fetchone()

    async def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[Mapping[str, Any]]:
        async with get_cursor(self.pool, dict_rows=True) as cur:
            await cur.execute(sql, params or ())
            rows = await cur.fetchall()
            return list(rows or [])

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        async with transaction(self.pool, dict_rows=False) as (_conn, cur):
            await cur.execute(sql, params or ())
            return cur.rowcount

    async def execute_many(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> int:
        async with transaction(self.pool, dict_rows=False) as (_conn, cur):
            await cur.executemany(sql, list(params_seq))
            return cur.rowcount


    async def insert_returning_id(self, sql: str, params: Sequence[Any] | None = None) -> int:
        async with transaction(self.pool, dict_rows=False) as (_conn, cur):
            await cur.execute(sql, params or ())
            return int(cur.lastrowid)

    async def in_tx(self, fn):
        """
        Run a function inside a transaction.
        The function receives (conn, cur).
        """
        async with transaction(self.pool, dict_rows=True) as (conn, cur):
            return await fn(conn, cur)
