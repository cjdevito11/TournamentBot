# cogs/admin_cog.py
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

import discord
from discord import app_commands
from discord.ext import commands

from repositories.identity_repo import IdentityRepo
from renderers.embeds import Embeds


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


class AdminCog(commands.Cog):
    admin = app_commands.Group(name="admin", description="Admin configuration (D2 Hustlers bot).")

    def __init__(self, bot: commands.Bot, *, identity_repo: IdentityRepo, embeds: Embeds) -> None:
        self.bot = bot
        self.identity_repo = identity_repo
        self.embeds = embeds

    async def _ensure_guild_channel_id(self, guild: discord.Guild) -> int:
        return await self.identity_repo.ensure_discord_guild(guild_id=guild.id, guild_name=guild.name)

    async def _get_guild_row(self, guild_channel_id: int) -> Optional[Mapping[str, Any]]:
        return await self.identity_repo.fetch_one(
            "SELECT channel_id, metadata FROM channel WHERE channel_id=%s;",
            (guild_channel_id,),
        )

    async def _set_guild_metadata_patch(self, guild_channel_id: int, patch: Mapping[str, Any]) -> None:
        patch_json = json.dumps(patch, separators=(",", ":"), ensure_ascii=False)
        await self.identity_repo.execute(
            """
            UPDATE channel
            SET metadata = JSON_MERGE_PATCH(COALESCE(metadata, JSON_OBJECT()), %s)
            WHERE channel_id=%s;
            """,
            (patch_json, guild_channel_id),
        )

    @admin.command(name="set_announce_channel", description="Set the default announce channel for events in this server.")
    @app_commands.describe(channel="The channel to post event announcements / bracket updates.")
    async def set_announce_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild_channel_id = await self._ensure_guild_channel_id(interaction.guild)
        announce_channel_id = await self.identity_repo.ensure_discord_text_channel(
            channel_id=channel.id,
            channel_name=channel.name,
            guild_id=interaction.guild.id,
        )

        await self._set_guild_metadata_patch(
            guild_channel_id,
            {
                "announce_channel_id": announce_channel_id,     # internal FK channel.channel_id
                "announce_discord_id": str(channel.id),        # convenience
            },
        )

        e = self.embeds.success(
            title="Announce channel set",
            description=f"Default announce channel is now {channel.mention}.",
        )
        await interaction.followup.send(embed=e, ephemeral=True)

    @admin.command(name="show_settings", description="Show current guild configuration.")
    async def show_settings(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild_channel_id = await self._ensure_guild_channel_id(interaction.guild)
        row = await self._get_guild_row(guild_channel_id)
        md = _json_obj(row["metadata"]) if row else {}

        announce_internal = md.get("announce_channel_id")
        announce_discord = md.get("announce_discord_id")

        desc = []
        if announce_discord:
            desc.append(f"**Announce channel:** <#{announce_discord}>")
        elif announce_internal:
            desc.append(f"**Announce channel:** (configured internally: {announce_internal})")
        else:
            desc.append("**Announce channel:** (not set)")

        e = self.embeds.info(title="Guild Settings", description="\n".join(desc))
        await interaction.followup.send(embed=e, ephemeral=True)


async def setup(bot: commands.Bot, *, identity_repo: IdentityRepo, embeds: Embeds) -> None:
    await bot.add_cog(AdminCog(bot, identity_repo=identity_repo, embeds=embeds))
