# main.py
from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import asdict
from typing import Optional

import discord
from discord.ext import commands

from config import load_config
from db.pool import DbPool, MySqlPoolConfig

from repositories.identity_repo import IdentityRepo
from repositories.team_repo import TeamRepo
from repositories.event_repo import EventRepo
from repositories.stats_repo import StatsRepo

from services.bracket_service import BracketService
from services.stats_service import StatsService

from renderers.embeds import Embeds
from renderers.bracket_view import BracketView
from renderers.leaderboard_view import LeaderboardView

from cogs.admin_cog import setup as setup_admin_cog
from cogs.events_cog import setup as setup_events_cog
from cogs.ladder_reset_cog import setup as setup_ladder_cog


class D2HBot(commands.Bot):
    def __init__(self) -> None:
        self.cfg = load_config()

        intents = discord.Intents.default()
        super().__init__(
            command_prefix=self.cfg.command_prefix,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        self.db: Optional[DbPool] = None

    async def setup_hook(self) -> None:
        logging.info("Starting setup_hook...")

        # --- DB ---
        self.db = DbPool()
        await self.db.start(MySqlPoolConfig(**asdict(self.cfg.mysql)))

        # --- Repos ---
        identity_repo = IdentityRepo(self.db)
        team_repo = TeamRepo(self.db)
        event_repo = EventRepo(self.db)
        stats_repo = StatsRepo(self.db)

        # --- Services ---
        bracket_service = BracketService(event_repo=event_repo)
        stats_service = StatsService(
            event_repo=event_repo,
            stats_repo=stats_repo,
            bracket_service=bracket_service,
        )

        # --- Renderers ---
        embeds = Embeds()
        bracket_view = BracketView()
        leaderboard_view = LeaderboardView()

        # --- Cogs ---
        await setup_admin_cog(self, identity_repo=identity_repo, embeds=embeds)
        await setup_events_cog(
            self,
            identity_repo=identity_repo,
            event_repo=event_repo,
            bracket_service=bracket_service,
            stats_service=stats_service,
            embeds=embeds,
            bracket_view=bracket_view,
            leaderboard_view=leaderboard_view,
        )
        await setup_ladder_cog(self, identity_repo=identity_repo, team_repo=team_repo, embeds=embeds)

        # --- Slash sync ---
        if self.cfg.dev_guild_id:
            guild = discord.Object(id=self.cfg.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logging.info("Slash commands synced to DEV guild %s", self.cfg.dev_guild_id)
        else:
            await self.tree.sync()
            logging.info("Slash commands synced globally")

        logging.info("setup_hook complete.")

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            if self.db:
                await self.db.close()
                self.db = None


async def _run_bot() -> None:
    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot = D2HBot()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(*_args) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    async with bot:
        await bot.start(cfg.token)
        await stop_event.wait()
        await bot.close()


def main() -> None:
    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
