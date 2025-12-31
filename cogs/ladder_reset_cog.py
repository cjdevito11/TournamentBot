# cogs/ladder_reset_cog.py
from __future__ import annotations

import aiomysql
import discord
from discord import app_commands
from discord.ext import commands

from repositories.identity_repo import IdentityRepo
from repositories.team_repo import TeamRepo
from renderers.embeds import Embeds


LADDER_CONTEXT = "ladder_reset"


class LadderResetCog(commands.Cog):
    ladder = app_commands.Group(name="ladder", description="Ladder reset squads (persistent teams).")

    def __init__(self, bot: commands.Bot, *, identity_repo: IdentityRepo, team_repo: TeamRepo, embeds: Embeds) -> None:
        self.bot = bot
        self.identity_repo = identity_repo
        self.team_repo = team_repo
        self.embeds = embeds

    async def _ensure_guild_channel_id(self, guild: discord.Guild) -> int:
        return await self.identity_repo.ensure_discord_guild(guild_id=guild.id, guild_name=guild.name)

    async def _ensure_account_id(self, member: discord.abc.User) -> int:
        return await self.identity_repo.upsert_discord_account(
            discord_user_id=member.id,
            display_name=getattr(member, "display_name", None) or getattr(member, "name", None),
            is_bot=getattr(member, "bot", None),
            metadata={"source": "discord"},
        )

    @ladder.command(name="team_create", description="Create a ladder squad (persistent).")
    @app_commands.describe(name="Team name", tag="Optional short tag")
    async def team_create(self, interaction: discord.Interaction, name: str, tag: str | None = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        guild_channel_id = await self._ensure_guild_channel_id(interaction.guild)
        captain_id = await self._ensure_account_id(interaction.user)

        try:
            team_id = await self.team_repo.create_team(
                guild_channel_id=guild_channel_id,
                context=LADDER_CONTEXT,
                name=name.strip()[:128],
                tag=(tag.strip()[:16] if tag else None),
                captain_account_id=captain_id,
                metadata={"source": "discord"},
            )
        except aiomysql.IntegrityError:
            await interaction.followup.send(
                embed=self.embeds.error(title="Name taken", description="A team with that name already exists in this server."),
                ephemeral=True,
            )
            return

        await self.team_repo.add_member(team_id=team_id, account_id=captain_id, role="starter", slot=1, metadata=None)

        e = self.embeds.success(title="Team created", description=f"**{name}** created. Team ID: `{team_id}`")
        await interaction.followup.send(embed=e, ephemeral=True)

    @ladder.command(name="team_join", description="Join an existing ladder team by name.")
    @app_commands.describe(team_name="Exact team name")
    async def team_join(self, interaction: discord.Interaction, team_name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        guild_channel_id = await self._ensure_guild_channel_id(interaction.guild)
        acct = await self._ensure_account_id(interaction.user)

        team = await self.team_repo.get_team_by_name(guild_channel_id=guild_channel_id, context=LADDER_CONTEXT, name=team_name)
        if not team:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Team not found."), ephemeral=True)
            return

        await self.team_repo.add_member(team_id=int(team["team_id"]), account_id=acct, role="starter", slot=None, metadata=None)
        await interaction.followup.send(embed=self.embeds.success(title="Joined", description=f"You joined **{team_name}**."), ephemeral=True)

    @ladder.command(name="team_roster", description="Show roster for a ladder team by name.")
    async def team_roster(self, interaction: discord.Interaction, team_name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)

        guild_channel_id = await self._ensure_guild_channel_id(interaction.guild)
        team = await self.team_repo.get_team_by_name(guild_channel_id=guild_channel_id, context=LADDER_CONTEXT, name=team_name)
        if not team:
            await interaction.followup.send(embed=self.embeds.error(title="Not found", description="Team not found."))
            return

        roster = await self.team_repo.get_roster(team_id=int(team["team_id"]))
        starters = [r for r in roster if str(r.get("role")) == "starter"]
        backups = [r for r in roster if str(r.get("role")) != "starter"]

        def fmt(rs):
            if not rs:
                return "(none)"
            return "\n".join([f"- {r.get('display_name')} (acct:{r.get('account_id')})" for r in rs])

        e = self.embeds.info(title=f"Roster: {team_name}")
        e.add_field(name="Starters", value=fmt(starters)[:1024], inline=False)
        e.add_field(name="Backups", value=fmt(backups)[:1024], inline=False)
        await interaction.followup.send(embed=e)

    @ladder.command(name="team_list", description="List ladder teams in this server.")
    async def team_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)

        guild_channel_id = await self._ensure_guild_channel_id(interaction.guild)
        teams = await self.team_repo.list_teams(guild_channel_id=guild_channel_id, context=LADDER_CONTEXT)
        if not teams:
            await interaction.followup.send(embed=self.embeds.warning(title="No teams", description="No ladder teams yet."))
            return

        lines = []
        for t in teams[:25]:
            lines.append(f"- **{t['name']}** (id `{t['team_id']}`)")

        e = self.embeds.info(title="Ladder Teams", description="\n".join(lines))
        await interaction.followup.send(embed=e)


async def setup(bot: commands.Bot, *, identity_repo: IdentityRepo, team_repo: TeamRepo, embeds: Embeds) -> None:
    await bot.add_cog(LadderResetCog(bot, identity_repo=identity_repo, team_repo=team_repo, embeds=embeds))
