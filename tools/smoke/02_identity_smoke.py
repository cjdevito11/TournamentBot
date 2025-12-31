from __future__ import annotations

import os, sys
from dataclasses import asdict
from uuid import uuid4

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
from repositories.identity_repo import IdentityRepo

async def main() -> None:
    _maybe_load_dotenv()
    cfg = load_config()

    run_id = os.getenv("SMOKE_RUN_ID") or f"smk_{uuid4().hex[:10]}"
    guild_id = int(os.getenv("SMOKE_GUILD_ID") or "999000111222333444")
    channel_id = int(os.getenv("SMOKE_TEXT_CHANNEL_ID") or "999000111222333555")
    user_id = int(os.getenv("SMOKE_USER_ID") or "999000111222333666")

    db = DbPool()
    await db.start(MySqlPoolConfig(**asdict(cfg.mysql)))
    repo = IdentityRepo(db)

    guild_channel_id = await repo.ensure_discord_guild(guild_id=guild_id, guild_name=f"SMOKE_GUILD_{run_id}")
    text_channel_id = await repo.ensure_discord_text_channel(channel_id=channel_id, channel_name=f"smoke-{run_id}", guild_id=guild_id)
    account_id = await repo.upsert_discord_account(discord_user_id=user_id, display_name=f"SMOKE_USER_{run_id}", metadata={"source": "smoke_test", "run_id": run_id})

    await repo.ensure_channel_member(channel_id=text_channel_id, account_id=account_id, metadata={"source": "smoke_test", "run_id": run_id})

    acct = await repo.resolve_account(discord_user_id=user_id)
    ch = await repo.resolve_channel(discord_channel_id=channel_id)

    assert acct and int(acct["account_id"]) == account_id
    assert ch and int(ch["channel_id"]) == text_channel_id

    await db.close()
    print(f"OK: identity smoke passed. run_id={run_id} guild_channel_id={guild_channel_id} channel_id={text_channel_id} account_id={account_id}")

if __name__ == "__main__":
    asyncio.run(main())
