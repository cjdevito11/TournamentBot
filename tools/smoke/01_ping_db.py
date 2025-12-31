from __future__ import annotations

import os, sys
from dataclasses import asdict

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

def _maybe_load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        return

import asyncio
from config import load_config
from db.pool import DbPool, MySqlPoolConfig

async def main() -> None:
    _maybe_load_dotenv()
    cfg = load_config()

    db = DbPool()
    await db.start(MySqlPoolConfig(**asdict(cfg.mysql)))
    await db.ping()
    await db.close()

    print("OK: DB pool ping succeeded.")

if __name__ == "__main__":
    asyncio.run(main())
