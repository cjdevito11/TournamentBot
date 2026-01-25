# cogs/events_cog.py
from __future__ import annotations

import os
import json
import random
import time
import asyncio



import discord
from discord import app_commands
from discord.ext import commands

from pathlib import Path
from io import BytesIO
from typing import Any, Mapping, Optional

from repositories.identity_repo import IdentityRepo
from repositories.event_repo import EventRepo
from services.bracket_service import BracketService
from services.stats_service import StatsService
from renderers.embeds import Embeds
from renderers.bracket_view import BracketView
from renderers.leaderboard_view import LeaderboardView, LeaderboardOptions
from renderers.bracket_diagram import BracketDiagramRenderer
from renderers.spin_reveal import SpinRevealRenderer


def _json_obj(v: Any) -> dict:
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


class EventsCog(commands.Cog):
    event = app_commands.Group(name="event", description="Create and run tournaments / events.")

    # -----------------------------
    # RATE LIMITS (easy knobs)
    # -----------------------------
    # Per-user cooldown (seconds) for heavier commands
    RL_USER_SECONDS_HEAVY: int = 15

    # Per-channel cooldown (seconds) for heavier commands (prevents pile-ups in a busy channel)
    RL_CHANNEL_SECONDS_HEAVY: int = 10

    # Per-event cooldown (seconds) for heavier commands (prevents spam regenerations for same event)
    RL_EVENT_SECONDS_HEAVY: int = 8

    # If True: managers bypass rate limits
    RL_MANAGERS_BYPASS: bool = True

    # Which commands are considered "heavy"
    RL_HEAVY_COMMANDS: set[str] = {
        "create_bracket",
        "bracket_image",
        "current_round",
    }

    # Roles allowed to manage event registrations (case-insensitive role name match)
    MANAGER_ROLE_NAMES = {
        "iron wolf",
        "council",
        "overseer",
        "prime evils",
        "the prime evils",
        "event coordinator",
    }

    def __init__(
        self,
        bot: commands.Bot,
        *,
        identity_repo: IdentityRepo,
        event_repo: EventRepo,
        bracket_service: BracketService,
        stats_service: StatsService,
        embeds: Embeds,
        bracket_view: BracketView,
        leaderboard_view: LeaderboardView,
        bracket_diagram: BracketDiagramRenderer,
    ) -> None:
        self.bot = bot
        self.identity_repo = identity_repo
        self.event_repo = event_repo
        self.brackets = bracket_service
        self.stats = stats_service
        self.embeds = embeds
        self.bracket_view = bracket_view
        self.leaderboard_view = leaderboard_view
        self.bracket_diagram = bracket_diagram

        # rate-limit state (in-memory)
        self._rl_user_last: dict[tuple[int, str], float] = {}
        self._rl_channel_last: dict[tuple[int, str], float] = {}
        self._rl_event_last: dict[tuple[int, int, str], float] = {}

    # -----------------------------
    # Helpers
    # -----------------------------
    async def _spin_visual(
        self,
        *,
        interaction: discord.Interaction,
        title: str,
        names: list[str],
        frames: int = 14,
        delay: float = 0.25,
    ) -> None:
        """
        Visual hype spinner. Edits ONE message with PNG frames.
        """
        renderer = SpinRevealRenderer()

        msg = await interaction.followup.send(
            content="🎰 **Spinning…**",
            wait=True,
        )

        cursor = random.randint(0, max(0, len(names) - 1))

        for i in range(frames):
            random.shuffle(names)
            cursor = random.randint(0, max(0, len(names) - 1))

            png = renderer.render_frame(
                title=title,
                entries=names,
                cursor=cursor,
                phase="Spinning…",
            )

            file = discord.File(BytesIO(png), filename="spin.png")
            await msg.edit(attachments=[file])

            await asyncio.sleep(delay)

        # Final lock frame
        png = renderer.render_frame(
            title=title,
            entries=names,
            cursor=cursor,
            phase="LOCKED",
        )
        file = discord.File(BytesIO(png), filename="spin_final.png")
        await msg.edit(content="🔥 **Teams Locked**", attachments=[file])



    def _load_help_text(self, filename: str, *, fallback: str = "") -> str:
        """
        Loads markdown/text from /data/help so you can edit help copy without touching code.
        """
        candidates = [
            Path("data") / "help" / filename,  # run from repo root
            Path(__file__).resolve().parent.parent / "data" / "help" / filename,  # cogs/ -> project/data/help
            Path(__file__).resolve().parent / "data" / "help" / filename,  # cogs/data/help (alt)
        ]
        for p in candidates:
            try:
                if p.exists() and p.is_file():
                    return (p.read_text(encoding="utf-8", errors="ignore") or fallback).strip()
            except Exception:
                continue
        return (fallback or "Help file not found.").strip()

    def _has_manager_role(self, member: discord.Member) -> bool:
        want = {n.lower() for n in self.MANAGER_ROLE_NAMES}
        return any((r.name or "").lower() in want for r in getattr(member, "roles", []))

    async def _can_manage(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if isinstance(interaction.user, discord.Member):
            perms = interaction.user.guild_permissions
            if perms.manage_guild or perms.manage_channels:
                return True
            return self._has_manager_role(interaction.user)
        return False

    def _now(self) -> float:
        return time.monotonic()

    def _cooldown_left(self, last_ts: float, cooldown: int) -> int:
        left = int(round((last_ts + cooldown) - self._now()))
        return max(0, left)

    async def _rate_limit_heavy(
        self,
        interaction: discord.Interaction,
        *,
        command_name: str,
        event_id: Optional[int] = None,
    ) -> bool:
        """
        Returns True if allowed, False if blocked (and sends a friendly message).
        Applies user+channel+event cooldowns for heavy commands.
        """
        if command_name not in self.RL_HEAVY_COMMANDS:
            return True

        if self.RL_MANAGERS_BYPASS and await self._can_manage(interaction):
            return True

        user_id = int(getattr(interaction.user, "id", 0) or 0)
        channel_id = int(getattr(interaction.channel, "id", 0) or 0)

        now = self._now()
        blocks: list[str] = []

        # Per-user
        ku = (user_id, command_name)
        last_u = self._rl_user_last.get(ku)
        if last_u is not None:
            left = self._cooldown_left(last_u, self.RL_USER_SECONDS_HEAVY)
            if left > 0:
                blocks.append(f"user cooldown: {left}s")
        # Per-channel
        kc = (channel_id, command_name)
        last_c = self._rl_channel_last.get(kc)
        if last_c is not None:
            left = self._cooldown_left(last_c, self.RL_CHANNEL_SECONDS_HEAVY)
            if left > 0:
                blocks.append(f"channel cooldown: {left}s")

        # Per-event
        if event_id is not None:
            ke = (int(event_id), channel_id, command_name)
            last_e = self._rl_event_last.get(ke)
            if last_e is not None:
                left = self._cooldown_left(last_e, self.RL_EVENT_SECONDS_HEAVY)
                if left > 0:
                    blocks.append(f"event cooldown: {left}s")

        if blocks:
            msg = (
                "Slow down, hero. That command is heavy and can backlog the bot.\n"
                f"Blocked by: **{', '.join(blocks)}**\n"
                "Try again in a moment."
            )

            # If already deferred, use followup. Otherwise respond.
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass
            return False

        # Record stamps (only when allowed)
        self._rl_user_last[ku] = now
        self._rl_channel_last[kc] = now
        if event_id is not None:
            self._rl_event_last[(int(event_id), channel_id, command_name)] = now

        return True

    async def _build_teams_by_seed(self, event_id: int) -> dict[int, dict[str, Any]]:
        teams = await self.event_repo.list_event_teams(event_id=event_id)
        teams_by_seed: dict[int, dict[str, Any]] = {}
        for t in teams or []:
            seed = t.get("seed")
            if seed is None:
                continue
            teams_by_seed[int(seed)] = {
                "event_team_id": int(t["event_team_id"]),
                "display_name": t.get("display_name") or f"Team {seed}",
            }
        return teams_by_seed

    async def _render_bracket_png_bytes(self, event_id: int) -> Optional[bytes]:
        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            return None

        teams_by_seed = await self._build_teams_by_seed(event_id)
        matches = await self.event_repo.list_matches(event_id=event_id)

        if not teams_by_seed:
            return None

        png = self.bracket_diagram.render_png(
            event_id=event_id,
            event_format=str(ev.get("format") or "double_elim"),
            teams_by_seed=teams_by_seed,
            matches=list(matches or []),
            title=f"Event {event_id} Bracket",
        )
        return png

    async def _render_current_round_png_bytes(self, event_id: int) -> Optional[bytes]:
        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            return None

        teams_by_seed = await self._build_teams_by_seed(event_id)
        matches = await self.event_repo.list_matches(event_id=event_id)

        if not teams_by_seed:
            return None

        png = self.bracket_diagram.render_current_round_png(
            event_id=event_id,
            event_format=str(ev.get("format") or "double_elim"),
            teams_by_seed=teams_by_seed,
            matches=list(matches or []),
            title=f"Event {event_id} Current Matches",
            statuses=("open", "pending"),
            max_cards=24,
            cards_per_row=None,
        )
        return png

    async def _save_bracket_image_message_ref(self, event_id: int, channel_id: int, message_id: int) -> None:
        sql = """
        UPDATE event
        SET metadata = JSON_SET(
            COALESCE(metadata, JSON_OBJECT()),
            '$.bracket_image_channel_id', CAST(%s AS JSON),
            '$.bracket_image_message_id', CAST(%s AS JSON)
        )
        WHERE event_id = %s;
        """
        await self.event_repo.execute(sql, (int(channel_id), int(message_id), int(event_id)))

    async def _save_current_round_image_message_ref(self, event_id: int, channel_id: int, message_id: int) -> None:
        sql = """
        UPDATE event
        SET metadata = JSON_SET(
            COALESCE(metadata, JSON_OBJECT()),
            '$.current_round_image_channel_id', CAST(%s AS JSON),
            '$.current_round_image_message_id', CAST(%s AS JSON)
        )
        WHERE event_id = %s;
        """
        await self.event_repo.execute(sql, (int(channel_id), int(message_id), int(event_id)))

    async def _upsert_bracket_image_post(self, event_id: int, channel: discord.TextChannel) -> Optional[discord.Message]:
        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            return None

        png = await self._render_bracket_png_bytes(event_id)
        if not png:
            return None

        md = _json_obj(ev.get("metadata"))
        prior_channel_id = md.get("bracket_image_channel_id")
        prior_message_id = md.get("bracket_image_message_id")

        target_channel: discord.abc.MessageableChannel = channel
        if prior_channel_id:
            ch = self.bot.get_channel(int(prior_channel_id))
            if isinstance(ch, discord.TextChannel):
                target_channel = ch

        file_obj = discord.File(BytesIO(png), filename=f"event_{event_id}_bracket.png")
        content = f"**Event {event_id} Bracket** (auto-updated)"

        if prior_channel_id and prior_message_id and isinstance(target_channel, discord.TextChannel):
            try:
                msg = await target_channel.fetch_message(int(prior_message_id))
                await msg.edit(content=content, attachments=[file_obj])
                return msg
            except Exception:
                pass

        if isinstance(target_channel, discord.TextChannel):
            msg = await target_channel.send(content=content, file=file_obj)
            await self._save_bracket_image_message_ref(event_id, target_channel.id, msg.id)
            return msg

        return None

    async def _upsert_current_round_image_post(self, event_id: int, channel: discord.TextChannel) -> Optional[discord.Message]:
        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            return None

        png = await self._render_current_round_png_bytes(event_id)
        if not png:
            return None

        md = _json_obj(ev.get("metadata"))
        prior_channel_id = md.get("current_round_image_channel_id")
        prior_message_id = md.get("current_round_image_message_id")

        target_channel: discord.abc.MessageableChannel = channel
        if prior_channel_id:
            ch = self.bot.get_channel(int(prior_channel_id))
            if isinstance(ch, discord.TextChannel):
                target_channel = ch

        file_obj = discord.File(BytesIO(png), filename=f"event_{event_id}_current.png")
        content = f"**Event {event_id} Current Matches** (auto-updated)"

        if prior_channel_id and prior_message_id and isinstance(target_channel, discord.TextChannel):
            try:
                msg = await target_channel.fetch_message(int(prior_message_id))
                await msg.edit(content=content, attachments=[file_obj])
                return msg
            except Exception:
                pass

        if isinstance(target_channel, discord.TextChannel):
            msg = await target_channel.send(content=content, file=file_obj)
            await self._save_current_round_image_message_ref(event_id, target_channel.id, msg.id)
            return msg

        return None

    async def _ensure_guild_channel_id(self, guild: discord.Guild) -> int:
        return await self.identity_repo.ensure_discord_guild(guild_id=guild.id, guild_name=guild.name)

    async def _ensure_text_channel_id(self, channel: discord.TextChannel, guild: discord.Guild) -> int:
        return await self.identity_repo.ensure_discord_text_channel(
            channel_id=channel.id,
            channel_name=channel.name,
            guild_id=guild.id,
        )

    async def _ensure_account_id(self, member: discord.abc.User) -> int:
        username = getattr(member, "name", None) or "unknown"
        nickname = getattr(member, "display_name", None) or username

        if nickname and username and nickname != username:
            combined = f"{nickname} (@{username})"
        else:
            combined = nickname or username

        combined = str(combined)[:128]

        return await self.identity_repo.upsert_discord_account(
            discord_user_id=member.id,
            display_name=combined,
            is_bot=getattr(member, "bot", None),
            metadata={
                "source": "discord",
                "discord_username": str(username),
                "discord_nickname": str(nickname),
            },
        )

    async def _get_guild_announce_channel_internal_id(self, guild_channel_id: int) -> Optional[int]:
        row = await self.identity_repo.fetch_one("SELECT metadata FROM channel WHERE channel_id=%s;", (guild_channel_id,))
        if not row:
            return None
        md = _json_obj(row.get("metadata"))
        v = md.get("announce_channel_id")
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    # -----------------------------
    # Commands
    # -----------------------------

    @app_commands.command(name="help", description="Show bot help.")
    async def help(self, interaction: discord.Interaction) -> None:
        text = self._load_help_text("help.md", fallback="Help is not configured yet. Add `data/help/help.md`.")
        if len(text) <= 1900:
            await interaction.response.send_message(f"```md\n{text}\n```", ephemeral=True)
            return
        await interaction.response.send_message("```md\nHelp is long — sending in parts.\n```", ephemeral=True)
        for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)]:
            await interaction.followup.send(f"```md\n{chunk}\n```", ephemeral=True)

    @app_commands.command(name="commands", description="Show bot commands.")
    async def commands(self, interaction: discord.Interaction) -> None:
        text = self._load_help_text("commands.md", fallback="Commands list is not configured yet. Add `data/help/commands.md`.")
        if len(text) <= 1900:
            await interaction.response.send_message(f"```md\n{text}\n```", ephemeral=True)
            return
        await interaction.response.send_message("```md\nCommands list is long — sending in parts.\n```", ephemeral=True)
        for chunk in [text[i : i + 1900] for i in range(0, len(text), 1900)]:
            await interaction.followup.send(f"```md\n{chunk}\n```", ephemeral=True)

    @event.command(name="create", description="Create a new event (tournament).")
    @app_commands.describe(
        name="Event name",
        format="Bracket format",
        team_size="Team size: 1..4",
        max_players="Max players (default 48)",
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name="Single Elim", value="single_elim"),
            app_commands.Choice(name="Double Elim", value="double_elim"),
        ]
    )
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        format: app_commands.Choice[str],
        team_size: app_commands.Range[int, 1, 4],
        max_players: app_commands.Range[int, 2, 200] = 48,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this in a server text channel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)

        guild_channel_id = await self._ensure_guild_channel_id(interaction.guild)

        announce_internal = await self._get_guild_announce_channel_internal_id(guild_channel_id)
        if announce_internal is None:
            announce_internal = await self._ensure_text_channel_id(interaction.channel, interaction.guild)

        created_by = await self._ensure_account_id(interaction.user)

        event_id = await self.event_repo.create_event(
            guild_channel_id=guild_channel_id,
            announce_channel_id=announce_internal,
            name=name.strip()[:128],
            format=format.value,
            team_size=int(team_size),
            max_players=int(max_players),
            created_by_account_id=created_by,
            starts_at=None,
            rules_json=None,
            metadata={"created_in_discord_channel": str(interaction.channel.id)},
        )

        e = self.embeds.success(
            title="Event created",
            description=(
                f"**ID:** `{event_id}`\n"
                f"**Name:** {name}\n"
                f"**Format:** {format.value}\n"
                f"**Team size:** {team_size}v{team_size}\n"
                f"**Max players:** {max_players}\n\n"
                f"Next:\n"
                f"- `/event open {event_id}`\n"
                f"- `/event registrations {event_id}`"
            ),
        )
        await interaction.followup.send(embed=e)

    @event.command(name="open", description="Open registrations for an event.")
    async def open(self, interaction: discord.Interaction, event_id: int) -> None:
        if not await self._can_manage(interaction):
            await interaction.response.send_message("Missing permission to manage events here.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        await self.event_repo.set_event_status(event_id=event_id, status="open")
        e = self.embeds.success(title="Event opened", description=f"Registrations are now open for event `{event_id}`.")
        await interaction.followup.send(embed=e, ephemeral=True)

    @event.command(name="lock", description="Lock registrations for an event.")
    async def lock(self, interaction: discord.Interaction, event_id: int) -> None:
        if not await self._can_manage(interaction):
            await interaction.response.send_message("Missing permission to manage events here.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        await self.event_repo.set_event_status(event_id=event_id, status="locked")
        e = self.embeds.success(title="Event locked", description=f"Registrations locked for event `{event_id}`.")
        await interaction.followup.send(embed=e, ephemeral=True)

    @event.command(name="info", description="Show event details.")
    async def info(self, interaction: discord.Interaction, event_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(
                embed=self.embeds.error(title="Not found", description="Event not found."),
                ephemeral=True,
            )
            return

        e = self.embeds.info(title=f"Event {event_id}: {ev.get('name')}")
        e.add_field(name="Status", value=str(ev.get("status")), inline=True)
        e.add_field(name="Format", value=str(ev.get("format")), inline=True)
        e.add_field(name="Team Size", value=f"{int(ev.get('team_size'))}v{int(ev.get('team_size'))}", inline=True)
        e.add_field(name="Max Players", value=str(ev.get("max_players")), inline=True)
        await interaction.followup.send(embed=e, ephemeral=True)

    @event.command(name="join", description="Join an event (register).")
    async def join(self, interaction: discord.Interaction, event_id: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."), ephemeral=True)
            return

        status = str(ev.get("status") or "").lower()
        if status not in ("open", "draft"):
            await interaction.followup.send(embed=self.embeds.warning(title="Closed", description=f"Event is `{status}`."), ephemeral=True)
            return

        acct = await self._ensure_account_id(interaction.user)
        await self.event_repo.register_player(event_id=event_id, account_id=acct, metadata={"discord_user_id": str(interaction.user.id)})

        e = self.embeds.success(title="Registered", description=f"You are registered for event `{event_id}`.")
        await interaction.followup.send(embed=e, ephemeral=True)

    @event.command(name="drop", description="Drop from an event (unregister).")
    async def drop(self, interaction: discord.Interaction, event_id: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        acct = await self._ensure_account_id(interaction.user)
        n = await self.event_repo.drop_player(event_id=event_id, account_id=acct)

        if n <= 0:
            e = self.embeds.warning(title="No change", description="You were not active in that event (or already dropped).")
        else:
            e = self.embeds.success(title="Dropped", description=f"You have been dropped from event `{event_id}`.")
        await interaction.followup.send(embed=e, ephemeral=True)

    @event.command(name="add_player", description="Manager: add a member to an event registration.")
    @app_commands.describe(event_id="Event ID", member="Member to register")
    async def add_player(self, interaction: discord.Interaction, event_id: int, member: discord.Member) -> None:
        if not await self._can_manage(interaction):
            await interaction.response.send_message("You don’t have permission to manage registrations here.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."), ephemeral=True)
            return

        status = str(ev.get("status") or "").lower()
        if status not in ("draft", "open", "locked"):
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="Event not editable",
                    description=f"Event is `{status}`. Registrations are typically edited in draft/open/locked only.",
                ),
                ephemeral=True,
            )
            return

        acct_id = await self._ensure_account_id(member)
        await self.event_repo.register_player(
            event_id=event_id,
            account_id=acct_id,
            metadata={"discord_user_id": str(member.id), "added_by_discord_user_id": str(interaction.user.id)},
        )

        await interaction.followup.send(
            embed=self.embeds.success(title="Player added", description=f"Registered {member.mention} for event `{event_id}`."),
            ephemeral=True,
        )

    @event.command(name="remove_player", description="Manager: remove a member from an event registration.")
    @app_commands.describe(event_id="Event ID", member="Member to drop")
    async def remove_player(self, interaction: discord.Interaction, event_id: int, member: discord.Member) -> None:
        if not await self._can_manage(interaction):
            await interaction.response.send_message("You don’t have permission to manage registrations here.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."), ephemeral=True)
            return

        status = str(ev.get("status") or "").lower()
        if status not in ("draft", "open", "locked"):
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="Event not editable",
                    description=f"Event is `{status}`. Registrations are typically edited in draft/open/locked only.",
                ),
                ephemeral=True,
            )
            return

        acct_id = await self._ensure_account_id(member)
        n = await self.event_repo.drop_player(event_id=event_id, account_id=acct_id)

        if n <= 0:
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="No change",
                    description=f"{member.mention} wasn’t active in event `{event_id}` (or already dropped).",
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=self.embeds.success(title="Player removed", description=f"Dropped {member.mention} from event `{event_id}`."),
            ephemeral=True,
        )

    @event.command(name="registrations", description="List current registrations for an event.")
    async def registrations(self, interaction: discord.Interaction, event_id: int) -> None:
        await interaction.response.defer(ephemeral=False)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."))
            return

        regs = await self.event_repo.list_registrations(event_id=event_id)
        if not regs:
            await interaction.followup.send(embed=self.embeds.warning(title="No registrations", description="Nobody has registered yet."))
            return

        lines: list[str] = []
        lines.append(f"=== Event {event_id} Registrations ===")
        lines.append(f"Status: {str(ev.get('status') or '').lower()}")
        lines.append("")
        for i, r in enumerate(regs, start=1):
            name = str(r.get("display_name") or f"acct:{r.get('account_id')}")
            status = str(r.get("status") or "")
            lines.append(f"{i:02d}. {name}  [{status}]")

        await interaction.followup.send("```text\n" + "\n".join(lines).rstrip() + "\n```")

    @event.command(name="add_fake_registrations", description="Manager: add fake registrations to test scale.")
    @app_commands.describe(
        event_id="Event ID",
        count="How many fake players to add",
        name_prefix="Prefix for fake names",
    )
    async def add_fake_registrations(
        self,
        interaction: discord.Interaction,
        event_id: int,
        count: app_commands.Range[int, 1, 200],
        name_prefix: str = "FAKE",
    ) -> None:
        if not await self._can_manage(interaction):
            await interaction.response.send_message("You don’t have permission to manage registrations here.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."), ephemeral=True)
            return

        status = str(ev.get("status") or "").lower()
        if status not in ("draft", "open", "locked"):
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="Event not editable",
                    description=f"Event is `{status}`. Fake registrations are typically added in draft/open/locked only.",
                ),
                ephemeral=True,
            )
            return

        added = 0
        base_fake_id = 99_000_000_000_000_000

        for _ in range(int(count)):
            fake_discord_id = base_fake_id + random.randint(1, 9_999_999_999)
            fake_name = f"{name_prefix}_{fake_discord_id % 100000:05d}"
            acct_id = await self.identity_repo.upsert_discord_account(
                discord_user_id=int(fake_discord_id),
                display_name=str(fake_name)[:128],
                is_bot=True,
                metadata={"source": "fake", "generated_by": str(interaction.user.id)},
            )

            await self.event_repo.register_player(
                event_id=event_id,
                account_id=int(acct_id),
                metadata={"fake": True, "generated_by_discord_user_id": str(interaction.user.id)},
            )
            added += 1

        await interaction.followup.send(
            embed=self.embeds.success(
                title="Fake registrations added",
                description=f"Added **{added}** fake players to event `{event_id}`.\nUse `/event registrations {event_id}` to verify.",
            ),
            ephemeral=True,
        )

    @event.command(name="randomize_teams", description="Randomize teams from registrations and create event_team records.")
    async def randomize_teams(self, interaction: discord.Interaction, event_id: int) -> None:
        if not await self._can_manage(interaction):
            await interaction.response.send_message("Missing permission to manage events here.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."))
            return

        status = str(ev.get("status") or "").lower()
        if status not in ("open", "locked", "draft"):
            await interaction.followup.send(embed=self.embeds.warning(title="Invalid state", description=f"Event is `{status}`."))
            return

        existing = await self.event_repo.list_event_teams(event_id=event_id)
        if existing:
            await interaction.followup.send(embed=self.embeds.warning(title="Already created", description="Event teams already exist."))
            return

        team_size = int(ev.get("team_size") or 2)
        max_players = int(ev.get("max_players") or 48)

        regs = await self.event_repo.list_registrations(event_id=event_id)
        active = [r for r in regs if str(r.get("status") or "").lower() == "active"]
        if len(active) < team_size * 2:
            await interaction.followup.send(embed=self.embeds.warning(title="Not enough players", description=f"Need at least {team_size*2} active registrations."))
            return

        if len(active) > max_players:
            active = active[:max_players]

        if len(active) % team_size != 0:
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="Team size mismatch",
                    description=f"Active registrations ({len(active)}) must be divisible by team size ({team_size}).",
                )
            )
            return

        random.shuffle(active)

        # ---- SPIN VISUAL (HYPE MOMENT) ----
        display_names = [
            str(r.get("display_name") or r.get("account_id"))
            for r in active
        ]

        await self._spin_visual(
            interaction=interaction,
            title="D2 Hustlers — Team Randomizer",
            names=display_names,
            frames=16,      # adjust safely
            delay=0.22,     # adjust speed
        )
        # ---------------------------------


        created_team_ids: list[int] = []
        team_no = 1
        for i in range(0, len(active), team_size):
            chunk = active[i : i + team_size]
            names = [str(c.get("display_name") or c.get("account_id")) for c in chunk]
            display_name = " + ".join(names)[:128]

            event_team_id = await self.event_repo.create_event_team(
                event_id=event_id,
                base_team_id=None,
                display_name=display_name,
                seed=team_no,
                metadata={"generated": True},
            )
            created_team_ids.append(event_team_id)

            for slot, r in enumerate(chunk, start=1):
                await self.event_repo.add_event_team_member(
                    event_team_id=event_team_id,
                    account_id=int(r["account_id"]),
                    role="starter",
                    slot=slot,
                    metadata=None,
                )

            team_no += 1

        await self.event_repo.set_event_status(event_id=event_id, status="locked")

        e = self.embeds.success(
            title="Teams created",
            description=f"Created **{len(created_team_ids)}** teams for event `{event_id}`.\nNext: `/event create_bracket {event_id}`",
        )
        await interaction.followup.send(embed=e)

    @event.command(name="create_bracket", description="Generate the bracket matches from event teams.")
    async def create_bracket(self, interaction: discord.Interaction, event_id: int) -> None:
        # rate-limit heavy command
        if not await self._rate_limit_heavy(interaction, command_name="create_bracket", event_id=event_id):
            return

        if not await self._can_manage(interaction):
            await interaction.response.send_message("Missing permission to manage events here.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)

        try:
            await self.brackets.create_bracket(event_id=event_id)
        except Exception as ex:
            await interaction.followup.send(embed=self.embeds.error(title="Bracket error", description=str(ex)))
            return

        await self.event_repo.set_event_status(event_id=event_id, status="active")
        await interaction.followup.send(embed=self.embeds.success(title="Bracket created", description=f"Bracket generated for event `{event_id}`."))

        if isinstance(interaction.channel, discord.TextChannel):
            await self._upsert_bracket_image_post(event_id, interaction.channel)
            await self._upsert_current_round_image_post(event_id, interaction.channel)

    @event.command(name="bracket_image", description="Show the current bracket as a drawn PNG.")
    async def bracket_image(self, interaction: discord.Interaction, event_id: int) -> None:
        # rate-limit heavy command
        if not await self._rate_limit_heavy(interaction, command_name="bracket_image", event_id=event_id):
            return

        await interaction.response.defer(ephemeral=False)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."))
            return

        teams_by_seed = await self._build_teams_by_seed(event_id)
        matches = await self.event_repo.list_matches(event_id=event_id)

        if not teams_by_seed:
            await interaction.followup.send(embed=self.embeds.warning(title="No teams", description="No event teams found yet."))
            return

        png = self.bracket_diagram.render_png(
            event_id=event_id,
            event_format=str(ev.get("format") or "double_elim"),
            teams_by_seed=teams_by_seed,
            matches=list(matches or []),
            title=f"Event {event_id} Bracket",
        )

        fp = BytesIO(png)
        await interaction.followup.send(file=discord.File(fp, filename=f"event_{event_id}_bracket.png"))

    @event.command(name="current_round", description="Show current active matches as an easy-to-read image.")
    async def current_round(self, interaction: discord.Interaction, event_id: int) -> None:
        # rate-limit heavy command
        if not await self._rate_limit_heavy(interaction, command_name="current_round", event_id=event_id):
            return

        await interaction.response.defer(ephemeral=False)

        ev = await self.event_repo.get_event(event_id=event_id)
        if not ev:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."))
            return

        png = await self._render_current_round_png_bytes(event_id)
        if not png:
            await interaction.followup.send(embed=self.embeds.warning(title="Nothing to show", description="No active matches found (or no teams yet)."))
            return

        fp = BytesIO(png)
        await interaction.followup.send(file=discord.File(fp, filename=f"event_{event_id}_current_round.png"))

    # ---- keep the rest of your commands unchanged below this line ----

    @event.command(name="leaderboard", description="Show event leaderboard (players + teams).")
    async def leaderboard(self, interaction: discord.Interaction, event_id: int) -> None:
        await interaction.response.defer(ephemeral=False)

        players = await self.stats.get_player_leaderboard(event_id=event_id)
        teams = await self.stats.get_team_records(event_id=event_id)

        if not players and not teams:
            await interaction.followup.send(embed=self.embeds.warning(title="No stats yet", description="No completed matches/stat lines found."))
            return

        if players:
            text_players = self.leaderboard_view.render_players(
                players,
                opts=LeaderboardOptions(title=f"Event {event_id}"),
            )
            await interaction.followup.send(content=text_players)

        if teams:
            text_teams = self.leaderboard_view.render_teams(teams, title=f"Event {event_id}")
            await interaction.followup.send(content=text_teams)

    @event.command(name="report", description="Report a match winner (and advance bracket).")
    @app_commands.describe(
        event_id="Event ID",
        match_code="Match code: W1-01, L2-03, GF-01",
        winner_seed="Winner seed number (from bracket lines: [seed])",
    )
    async def report(self, interaction: discord.Interaction, event_id: int, match_code: str, winner_seed: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)

        reporter = await self._ensure_account_id(interaction.user)

        m = await self.event_repo.get_match_by_code(event_id=event_id, match_code=match_code)
        if not m:
            await interaction.followup.send(
                embed=self.embeds.error(
                    title="Report failed",
                    description="Match not found. Use a code like `W1-01`, `L2-03`, or `GF-01`.",
                )
            )
            return

        row = await self.event_repo.fetch_one(
            "SELECT event_team_id FROM event_team WHERE event_id=%s AND seed=%s;",
            (int(event_id), int(winner_seed)),
        )
        if not row:
            await interaction.followup.send(
                embed=self.embeds.error(
                    title="Report failed",
                    description=f"Winner seed `{winner_seed}` not found for event `{event_id}`.",
                )
            )
            return

        winner_event_team_id = int(row["event_team_id"])
        match_id = int(m["event_match_id"])

        try:
            _ = await self.stats.report_match(
                event_match_id=match_id,
                winner_event_team_id=winner_event_team_id,
                reported_by_account_id=reporter,
                player_stats=None,
                metadata={"source": "discord", "match_code": str(match_code).upper(), "winner_seed": int(winner_seed)},
            )
        except Exception as ex:
            await interaction.followup.send(embed=self.embeds.error(title="Report failed", description=str(ex)))
            return

        await interaction.followup.send(
            embed=self.embeds.success(
                title="Match recorded",
                description=(f"Recorded `{str(match_code).upper()}` winner seed `{winner_seed}`.\nBracket images will be updated automatically."),
            )
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await self._upsert_bracket_image_post(event_id, interaction.channel)
            await self._upsert_current_round_image_post(event_id, interaction.channel)



async def setup(
    bot: commands.Bot,
    *,
    identity_repo: IdentityRepo,
    event_repo: EventRepo,
    bracket_service: BracketService,
    stats_service: StatsService,
    embeds: Embeds,
    bracket_view: BracketView,
    leaderboard_view: LeaderboardView,
    bracket_diagram: BracketDiagramRenderer,
) -> None:
    await bot.add_cog(
        EventsCog(
            bot,
            identity_repo=identity_repo,
            event_repo=event_repo,
            bracket_service=bracket_service,
            stats_service=stats_service,
            embeds=embeds,
            bracket_view=bracket_view,
            leaderboard_view=leaderboard_view,
            bracket_diagram=bracket_diagram,
        )
    )
