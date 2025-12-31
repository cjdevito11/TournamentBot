# services/identity_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

from repositories.identity_repo import IdentityRepo


@dataclass(frozen=True)
class IdentityResult:
    account_id: int
    guild_channel_id: int
    text_channel_id: Optional[int]


class IdentityService:
    """
    Converts Discord runtime objects (guild, member, channel)
    into stable DB identities using your generic tables.

    This must be called early in every command so:
      - platform_account is upserted
      - channel rows for guild/text channel exist
      - channel_member is maintained
    """

    def __init__(self, identity_repo: IdentityRepo) -> None:
        self._repo = identity_repo

    async def ensure_context(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member | discord.User,
        channel: discord.abc.GuildChannel | discord.Thread | None,
        is_mod: bool | None = None,
        extra_user_metadata: Mapping[str, Any] | None = None,
        extra_channel_metadata: Mapping[str, Any] | None = None,
    ) -> IdentityResult:
        # 1) Ensure guild exists as a channel row
        guild_channel_id = await self._repo.ensure_discord_guild(
            guild_id=guild.id,
            guild_name=guild.name,
        )

        # 2) Ensure text channel exists (if provided)
        text_channel_id: int | None = None
        if channel is not None:
            # Thread has parent
            if isinstance(channel, discord.Thread):
                ch_id = channel.id
                ch_name = channel.name
            else:
                ch_id = channel.id
                ch_name = getattr(channel, "name", None)

            md = {"guild_id": str(guild.id)}
            if extra_channel_metadata:
                md.update(extra_channel_metadata)

            text_channel_id = await self._repo.ensure_discord_text_channel(
                channel_id=ch_id,
                channel_name=ch_name,
                guild_id=guild.id,
            )

        # 3) Upsert user
        # Prefer Member display name when available
        display_name = None
        if isinstance(member, discord.Member):
            display_name = member.display_name
        else:
            # discord.User has global_name in newer APIs; fall back to name
            display_name = getattr(member, "global_name", None) or member.name

        md_user = {}
        # Store useful Discord identifiers without using them as unique keys
        md_user["discord_name"] = getattr(member, "name", None)
        md_user["discord_discriminator"] = getattr(member, "discriminator", None)
        if extra_user_metadata:
            md_user.update(extra_user_metadata)

        account_id = await self._repo.upsert_discord_account(
            discord_user_id=member.id,
            display_name=display_name,
            is_bot=getattr(member, "bot", None),
            is_mod=is_mod,
            metadata=md_user,
        )

        # 4) Ensure membership row exists for the guild "channel"
        await self._repo.ensure_channel_member(
            channel_id=guild_channel_id,
            account_id=account_id,
            roles_json=None,
            metadata={"source": "discord"},
        )

        return IdentityResult(
            account_id=account_id,
            guild_channel_id=guild_channel_id,
            text_channel_id=text_channel_id,
        )
