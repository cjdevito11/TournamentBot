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
from repositories.team_repo import TeamRepo

async def main() -> None:
    _maybe_load_dotenv()
    cfg = load_config()

    run_id = os.getenv("SMOKE_RUN_ID") or f"smk_{uuid4().hex[:10]}"
    guild_id = int(os.getenv("SMOKE_GUILD_ID") or "999000111222333444")

    db = DbPool()
    await db.start(MySqlPoolConfig(**asdict(cfg.mysql)))

    ident = IdentityRepo(db)
    teams = TeamRepo(db)

    guild_channel_id = await ident.ensure_discord_guild(guild_id=guild_id, guild_name=f"SMOKE_GUILD_{run_id}")

    a1 = await ident.upsert_discord_account(discord_user_id=910000000000000001, display_name=f"SMOKE_A1_{run_id}", metadata={"source": "smoke_test", "run_id": run_id})
    a2 = await ident.upsert_discord_account(discord_user_id=910000000000000002, display_name=f"SMOKE_A2_{run_id}", metadata={"source": "smoke_test", "run_id": run_id})

    team_id = await teams.create_team(
        guild_channel_id=guild_channel_id,
        context="ladder_reset",
        name=f"SMOKE_TEAM_{run_id}",
        tag="SMK",
        captain_account_id=a1,
        metadata={"source": "smoke_test", "run_id": run_id},
    )

    await teams.add_member(team_id=team_id, account_id=a1, role="starter", slot=1)
    await teams.add_member(team_id=team_id, account_id=a2, role="backup", slot=None)

    roster = await teams.get_roster(team_id=team_id)
    assert len(roster) >= 2

    await db.close()
    print(f"OK: team repo smoke passed. run_id={run_id} team_id={team_id} roster_count={len(roster)}")

if __name__ == "__main__":
    asyncio.run(main())
