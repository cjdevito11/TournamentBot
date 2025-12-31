# db/pool.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import aiomysql


@dataclass(frozen=True)
class MySqlPoolConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    minsize: int = 1
    maxsize: int = 5
    connect_timeout: int = 10


class DbPool:
    """
    Central DB pool lifecycle manager.
    - Create once at startup
    - Reuse pool everywhere (repositories)
    - Close on shutdown
    """

    def __init__(self) -> None:
        self._pool: Optional[aiomysql.Pool] = None

    @property
    def pool(self) -> aiomysql.Pool:
        if self._pool is None:
            raise RuntimeError("DB pool is not initialized. Call await DbPool.start() first.")
        return self._pool

    async def start(self, cfg: MySqlPoolConfig) -> None:
        if self._pool is not None:
            return

        self._pool = await aiomysql.create_pool(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            db=cfg.database,
            minsize=cfg.minsize,
            maxsize=cfg.maxsize,
            connect_timeout=cfg.connect_timeout,
            autocommit=True,  # repositories can do single statements without explicit commit
            charset="utf8mb4",
        )

        # sanity check connection works immediately
        await self.ping()

    async def ping(self) -> None:
        """
        Verifies pool is usable. Raises if not.
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1;")
                await cur.fetchone()

    async def close(self) -> None:
        if self._pool is None:
            return
        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None
