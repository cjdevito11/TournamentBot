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
from db.tx import get_cursor

async def main() -> None:
    _maybe_load_dotenv()
    cfg = load_config()

    run_id = os.getenv("SMOKE_RUN_ID")
    if not run_id:
        raise RuntimeError("Set SMOKE_RUN_ID to the run_id you want to clean up.")

    db = DbPool()
    await db.start(MySqlPoolConfig(**asdict(cfg.mysql)))

    # Delete in FK-safe order
    statements = [
        # match stats -> matches
        ("DELETE s FROM event_match_player_stat s JOIN event_match m ON m.event_match_id=s.event_match_id "
         "WHERE JSON_EXTRACT(s.metadata,'$.source')='smoke_test' AND JSON_EXTRACT(s.metadata,'$.run_id')=%s;", (run_id,)),

        ("DELETE FROM event_match WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
        ("DELETE FROM event_team_member WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
        ("DELETE FROM event_team WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
        ("DELETE FROM event_registration WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
        ("DELETE FROM event WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),

        ("DELETE FROM team_member WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
        ("DELETE FROM team WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),

        ("DELETE FROM channel_member WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
        ("DELETE FROM channel WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
        ("DELETE FROM platform_account WHERE JSON_EXTRACT(metadata,'$.source')='smoke_test' AND JSON_EXTRACT(metadata,'$.run_id')=%s;", (run_id,)),
    ]

    async with get_cursor(db.pool, dict_rows=False) as cur:
        for sql, params in statements:
            await cur.execute(sql, params)
            print(f"OK: {cur.rowcount} rows affected")

    await db.close()
    print(f"OK: cleanup done for run_id={run_id}")

if __name__ == "__main__":
    asyncio.run(main())
