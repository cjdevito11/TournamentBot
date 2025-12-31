# db/tx.py
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Tuple

import aiomysql


@asynccontextmanager
async def get_conn(pool: aiomysql.Pool) -> AsyncIterator[aiomysql.Connection]:
    """
    Acquire a connection from the pool.
    """
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def get_cursor(
    pool: aiomysql.Pool, *, dict_rows: bool = True
) -> AsyncIterator[aiomysql.Cursor]:
    """
    Acquire a cursor (defaults to DictCursor for clean repo code).
    Autocommit should be True at pool level; this is for simple read/write statements.
    """
    cursor_cls = aiomysql.DictCursor if dict_rows else aiomysql.Cursor
    async with pool.acquire() as conn:
        async with conn.cursor(cursor_cls) as cur:
            yield cur


@asynccontextmanager
async def transaction(
    pool: aiomysql.Pool, *, dict_rows: bool = True
) -> AsyncIterator[Tuple[aiomysql.Connection, aiomysql.Cursor]]:
    """
    Runs statements inside a transaction.
    - Commits on success
    - Rolls back on exception

    Usage:
        async with transaction(pool) as (conn, cur):
            await cur.execute(...)
            ...
    """
    cursor_cls = aiomysql.DictCursor if dict_rows else aiomysql.Cursor

    async with pool.acquire() as conn:
        # aiomysql connections created with autocommit=True (pool setting),
        # but explicit begin/commit/rollback is still valid here.
        await conn.begin()
        try:
            async with conn.cursor(cursor_cls) as cur:
                yield conn, cur
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
