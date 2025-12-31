# repositories/identity_repo.py
from __future__ import annotations

from typing import Any, Mapping

from repositories.base_repo import BaseRepo, to_json


class IdentityRepo(BaseRepo):
    """
    Discord identity + channel upserts into your existing generic tables.

    Conventions (to avoid UNIQUE conflicts):
      - platform_account.external_user_id = Discord snowflake (string)
      - platform_account.username         = Discord snowflake (string)  <-- guarantees uniqueness
      - platform_account.display_name     = human friendly name
      - channel.external_channel_id       = Discord snowflake (string)
      - channel.external_channel_name     = Discord snowflake (string)  <-- guarantees uniqueness
      - channel.name                      = human friendly name
    """

    async def ensure_platform(self, name: str) -> int:
        await self.execute(
            "INSERT INTO platform (name, metadata) VALUES (%s, JSON_OBJECT('source','bot')) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name);",
            (name,),
        )
        row = await self.fetch_one("SELECT platform_id FROM platform WHERE name=%s;", (name,))
        if not row:
            raise RuntimeError(f"Failed to resolve platform_id for platform={name}")
        return int(row["platform_id"])

    async def ensure_discord_platform(self) -> int:
        return await self.ensure_platform("discord")

    async def upsert_discord_account(
        self,
        *,
        discord_user_id: int,
        display_name: str | None,
        is_bot: bool | None = None,
        is_mod: bool | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        platform_id = await self.ensure_discord_platform()
        snowflake = str(discord_user_id)

        # Use snowflake for username to satisfy UNIQUE(platform_id, username)
        await self.execute(
            """
            INSERT INTO platform_account
              (platform_id, external_user_id, username, display_name, is_bot, is_mod, metadata, first_seen_at, last_seen_at)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, NOW(6), NOW(6))
            ON DUPLICATE KEY UPDATE
              display_name = VALUES(display_name),
              is_bot       = COALESCE(VALUES(is_bot), is_bot),
              is_mod       = COALESCE(VALUES(is_mod), is_mod),
              metadata     = COALESCE(VALUES(metadata), metadata),
              last_seen_at = NOW(6);
            """,
            (
                platform_id,
                snowflake,
                snowflake,
                display_name or snowflake,
                1 if is_bot else 0 if is_bot is not None else None,
                1 if is_mod else 0 if is_mod is not None else None,
                to_json(metadata),
            ),
        )

        row = await self.fetch_one(
            "SELECT account_id FROM platform_account WHERE platform_id=%s AND username=%s;",
            (platform_id, snowflake),
        )
        if not row:
            raise RuntimeError("Failed to resolve account_id after upsert_discord_account()")
        return int(row["account_id"])

    async def upsert_discord_channel(
        self,
        *,
        discord_channel_id: int,
        human_name: str | None,
        channel_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        platform_id = await self.ensure_discord_platform()
        snowflake = str(discord_channel_id)

        await self.execute(
            """
            INSERT INTO channel
              (platform_id, external_channel_id, external_channel_name, channel_type, name, metadata)
            VALUES
              (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              channel_type = VALUES(channel_type),
              name         = VALUES(name),
              metadata     = COALESCE(VALUES(metadata), metadata);
            """,
            (
                platform_id,
                snowflake,
                snowflake,  # ensures UNIQUE(platform_id, external_channel_name) never collides
                channel_type,
                human_name or snowflake,
                to_json(metadata),
            ),
        )

        row = await self.fetch_one(
            "SELECT channel_id FROM channel WHERE platform_id=%s AND external_channel_name=%s;",
            (platform_id, snowflake),
        )
        if not row:
            raise RuntimeError("Failed to resolve channel_id after upsert_discord_channel()")
        return int(row["channel_id"])

    async def ensure_discord_guild(self, *, guild_id: int, guild_name: str | None) -> int:
        return await self.upsert_discord_channel(
            discord_channel_id=guild_id,
            human_name=guild_name,
            channel_type="discord_guild",
            metadata={"scope": "guild"},
        )

    async def ensure_discord_text_channel(
        self,
        *,
        channel_id: int,
        channel_name: str | None,
        guild_id: int | None = None,
    ) -> int:
        md = {"scope": "text"}
        if guild_id is not None:
            md["guild_id"] = str(guild_id)
        return await self.upsert_discord_channel(
            discord_channel_id=channel_id,
            human_name=channel_name,
            channel_type="discord_text",
            metadata=md,
        )

    async def ensure_channel_member(
        self,
        *,
        channel_id: int,
        account_id: int,
        roles_json: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        # channel_member has UNIQUE(channel_id, account_id)
        await self.execute(
            """
            INSERT INTO channel_member
              (channel_id, account_id, roles_json, metadata, first_seen_at, last_seen_at)
            VALUES
              (%s, %s, %s, %s, NOW(6), NOW(6))
            ON DUPLICATE KEY UPDATE
              roles_json   = COALESCE(VALUES(roles_json), roles_json),
              metadata     = COALESCE(VALUES(metadata), metadata),
              last_seen_at = NOW(6);
            """,
            (channel_id, account_id, to_json(roles_json), to_json(metadata)),
        )

    async def resolve_account(self, *, discord_user_id: int) -> Mapping[str, Any] | None:
        platform_id = await self.ensure_discord_platform()
        snowflake = str(discord_user_id)
        return await self.fetch_one(
            """
            SELECT account_id, platform_id, external_user_id, username, display_name
            FROM platform_account
            WHERE platform_id=%s AND username=%s;
            """,
            (platform_id, snowflake),
        )

    async def resolve_channel(self, *, discord_channel_id: int) -> Mapping[str, Any] | None:
        platform_id = await self.ensure_discord_platform()
        snowflake = str(discord_channel_id)
        return await self.fetch_one(
            """
            SELECT channel_id, platform_id, external_channel_id, external_channel_name, channel_type, name
            FROM channel
            WHERE platform_id=%s AND external_channel_name=%s;
            """,
            (platform_id, snowflake),
        )
