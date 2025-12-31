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
from repositories.event_repo import EventRepo
from services.bracket_service import BracketService

async def main() -> None:
    _maybe_load_dotenv()
    cfg = load_config()

    run_id = os.getenv("SMOKE_RUN_ID") or f"smk_{uuid4().hex[:10]}"
    guild_id = int(os.getenv("SMOKE_GUILD_ID") or "999000111222333444")
    announce_channel_discord_id = int(os.getenv("SMOKE_TEXT_CHANNEL_ID") or "999000111222333555")

    db = DbPool()
    await db.start(MySqlPoolConfig(**asdict(cfg.mysql)))

    ident = IdentityRepo(db)
    events = EventRepo(db)
    brackets = BracketService(event_repo=events)

    guild_channel_id = await ident.ensure_discord_guild(guild_id=guild_id, guild_name=f"SMOKE_GUILD_{run_id}")
    announce_channel_id = await ident.ensure_discord_text_channel(
        channel_id=announce_channel_discord_id,
        channel_name=f"smoke-announce-{run_id}",
        guild_id=guild_id,
    )

    # Create 2v2 event with 8 players => 4 teams
    event_id = await events.create_event(
        guild_channel_id=guild_channel_id,
        announce_channel_id=announce_channel_id,
        name=f"SMOKE_EVENT_{run_id}",
        format="double_elim",
        team_size=2,
        max_players=48,
        created_by_account_id=None,
        starts_at=None,
        rules_json={"source": "smoke_test", "run_id": run_id},
        metadata={"source": "smoke_test", "run_id": run_id},
    )

    # Register 8 players
    account_ids: list[int] = []
    for i in range(8):
        discord_user_id = 920000000000000000 + i
        acct = await ident.upsert_discord_account(
            discord_user_id=discord_user_id,
            display_name=f"SMOKE_P{i+1}_{run_id}",
            metadata={"source": "smoke_test", "run_id": run_id},
        )
        account_ids.append(acct)
        await events.register_player(event_id=event_id, account_id=acct, metadata={"source": "smoke_test", "run_id": run_id})

    regs = await events.list_registrations(event_id=event_id)
    assert len(regs) >= 8

    # Create 4 event teams (2 players each)
    for t in range(4):
        et_id = await events.create_event_team(
            event_id=event_id,
            base_team_id=None,
            display_name=f"SMOKE_TEAM_{t+1}_{run_id}",
            seed=t + 1,
            metadata={"source": "smoke_test", "run_id": run_id},
        )
        p1 = account_ids[t * 2]
        p2 = account_ids[t * 2 + 1]
        await events.add_event_team_member(event_team_id=et_id, account_id=p1, role="starter", slot=1, metadata={"source": "smoke_test", "run_id": run_id})
        await events.add_event_team_member(event_team_id=et_id, account_id=p2, role="starter", slot=2, metadata={"source": "smoke_test", "run_id": run_id})

    teams = await events.list_event_teams(event_id=event_id)
    assert len(teams) == 4

    # Generate matches via BracketService
    await brackets.create_bracket(event_id=event_id)
    matches = await events.list_matches(event_id=event_id)
    assert len(matches) > 0

    await db.close()
    print(f"OK: event smoke passed. run_id={run_id} event_id={event_id} teams={len(teams)} matches={len(matches)}")

if __name__ == "__main__":
    asyncio.run(main())
