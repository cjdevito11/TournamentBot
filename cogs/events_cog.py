# cogs/events_cog.py
from __future__ import annotations

import json
import random
from typing import Any, Mapping, Optional

import discord
from discord import app_commands
from discord.ext import commands

from repositories.identity_repo import IdentityRepo
from repositories.event_repo import EventRepo
from services.bracket_service import BracketService
from services.stats_service import StatsService
from renderers.embeds import Embeds
from renderers.bracket_view import BracketView
from renderers.leaderboard_view import LeaderboardView, LeaderboardOptions


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
    ) -> None:
        self.bot = bot
        self.identity_repo = identity_repo
        self.event_repo = event_repo
        self.brackets = bracket_service
        self.stats = stats_service
        self.embeds = embeds
        self.bracket_view = bracket_view
        self.leaderboard_view = leaderboard_view

    # -----------------------------
    # Helpers
    # -----------------------------

    async def _ensure_guild_channel_id(self, guild: discord.Guild) -> int:
        return await self.identity_repo.ensure_discord_guild(guild_id=guild.id, guild_name=guild.name)

    async def _ensure_text_channel_id(self, channel: discord.TextChannel, guild: discord.Guild) -> int:
        return await self.identity_repo.ensure_discord_text_channel(
            channel_id=channel.id,
            channel_name=channel.name,
            guild_id=guild.id,
        )

    async def _ensure_account_id(self, member: discord.abc.User) -> int:
        return await self.identity_repo.upsert_discord_account(
            discord_user_id=member.id,
            display_name=getattr(member, "display_name", None) or getattr(member, "name", None),
            is_bot=getattr(member, "bot", None),
            metadata={"source": "discord"},
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

    async def _can_manage(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if isinstance(interaction.user, discord.Member):
            return interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.manage_channels
        return False

    # -----------------------------
    # Commands
    # -----------------------------

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

        # announce channel: guild setting > current channel
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
            description=f"**ID:** `{event_id}`\n**Name:** {name}\n**Format:** {format.value}\n**Team size:** {team_size}v{team_size}\n**Max players:** {max_players}",
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
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Event not found."), ephemeral=True)
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
            await interaction.followup.send(embed=self.embeds.warning(title="Invalid state", description=f"Event is `{status}`."))  # public
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
            await interaction.followup.send(
                embed=self.embeds.warning(title="Not enough players", description=f"Need at least {team_size*2} active registrations.")
            )
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

        # Create teams
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

    @event.command(name="bracket", description="Show the current bracket.")
    async def bracket(self, interaction: discord.Interaction, event_id: int) -> None:
        await interaction.response.defer(ephemeral=False)

        teams = await self.event_repo.list_event_teams(event_id=event_id)
        matches = await self.event_repo.list_matches(event_id=event_id)
        if not teams:
            await interaction.followup.send(embed=self.embeds.warning(title="No teams", description="No event teams found yet."))
            return
        if not matches:
            await interaction.followup.send(embed=self.embeds.warning(title="No matches", description="No matches generated yet."))
            return

        text = self.bracket_view.render(matches=matches, teams=teams, title=f"Event {event_id} Bracket")

        e = self.embeds.info(title=f"Event {event_id} Bracket", description="Current bracket snapshot.")
        await interaction.followup.send(embed=e)
        await interaction.followup.send(content=text)

        # Reporting guidance (simple + copy/paste friendly)
        await interaction.followup.send(
            embed=self.embeds.info(
                title="Reporting results",
                description=(
                    "Use the match code and the winner seed shown in the bracket.\n\n"
                    f"Example:\n"
                    f"`/event report event_id:{event_id} match_code:W1-02 winner_seed:3`\n\n"
                    "Notes:\n"
                    "- `match_code` looks like `W1-02` or `L1-01` (exactly as shown)\n"
                    "- `winner_seed` is the number inside `[ ]` next to the winning team"
                ),
            )
        )


    @event.command(name="report", description="Report a match winner (by bracket code + seed) and advance bracket.")
    @app_commands.describe(
        event_id="Event ID",
        match_code="Bracket match code shown in /event bracket (ex: W1-02, L1-01, GF-01)",
        winner_seed="Winner seed shown in brackets (the number inside [ ]). Example: 3",
    )
    async def report(
        self,
        interaction: discord.Interaction,
        event_id: int,
        match_code: str,
        winner_seed: app_commands.Range[int, 1, 9999],
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)

        reporter = await self._ensure_account_id(interaction.user)

        # Resolve match from event_id + match_code (W1-02 style)
        m = await self.event_repo.get_match_by_code(event_id=event_id, match_code=match_code)
        if not m:
            await interaction.followup.send(
                embed=self.embeds.error(
                    title="Match not found",
                    description="Could not find that match code for this event.\n"
                                "Use `/event bracket <event_id>` and copy a code like `W1-02`.",
                )
            )
            return

        if str(m.get("status") or "").lower() == "completed":
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="Already completed",
                    description=f"Match `{match_code.upper()}` is already completed for event `{event_id}`.",
                )
            )
            return

        team1_id = int(m["team1_event_team_id"])
        team2_raw = m.get("team2_event_team_id")
        team2_id = int(team2_raw) if team2_raw is not None else None

        if team2_id is None:
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="BYE match",
                    description=f"Match `{match_code.upper()}` is a BYE. No report needed.",
                )
            )
            return

        # Map team_id -> seed (so user can report by winner_seed)
        teams = await self.event_repo.list_event_teams(event_id=event_id)
        seed_by_team: dict[int, int] = {}
        name_by_team: dict[int, str] = {}

        for t in teams:
            tid = int(t["event_team_id"])
            name_by_team[tid] = str(t.get("display_name") or f"Team {tid}")
            if t.get("seed") is not None:
                try:
                    seed_by_team[tid] = int(t["seed"])
                except Exception:
                    pass

        s1 = seed_by_team.get(team1_id)
        s2 = seed_by_team.get(team2_id)

        if s1 is None or s2 is None:
            await interaction.followup.send(
                embed=self.embeds.error(
                    title="Seed mapping missing",
                    description="This match does not have seeds assigned to both teams.\n"
                                "Make sure you created event teams with seeds (your randomize step does this).",
                )
            )
            return

        if int(winner_seed) not in (s1, s2):
            await interaction.followup.send(
                embed=self.embeds.warning(
                    title="Invalid winner_seed",
                    description=(
                        f"For match `{match_code.upper()}`, winner_seed must be `{s1}` or `{s2}`.\n"
                        f"Teams: `[${s1}] {name_by_team.get(team1_id, str(team1_id))}` vs "
                        f"`[{s2}] {name_by_team.get(team2_id, str(team2_id))}`"
                    ).replace("[$", "["),  # tiny safety to keep formatting intact
                )
            )
            return

        winner_team_id = team1_id if int(winner_seed) == s1 else team2_id

        try:
            updated_event_id = await self.stats.report_match(
                event_match_id=int(m["event_match_id"]),
                winner_event_team_id=int(winner_team_id),
                reported_by_account_id=reporter,
                player_stats=None,
                metadata={"source": "discord", "match_code": match_code.upper(), "winner_seed": int(winner_seed)},
            )
        except Exception as ex:
            await interaction.followup.send(embed=self.embeds.error(title="Report failed", description=str(ex)))
            return

        await interaction.followup.send(
            embed=self.embeds.success(
                title="Match recorded",
                description=(
                    f"Recorded `{match_code.upper()}` winner as seed `[{int(winner_seed)}]`.\n"
                    f"Bracket advanced for event `{updated_event_id}`.\n"
                    f"Use `/event bracket {updated_event_id}` to view."
                ),
            )
        )


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
        )
    )
