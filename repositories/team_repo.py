# repositories/team_repo.py
from __future__ import annotations

from typing import Any, Mapping

from repositories.base_repo import BaseRepo, to_json


class TeamRepo(BaseRepo):
    async def create_team(
        self,
        *,
        guild_channel_id: int,
        context: str,
        name: str,
        tag: str | None = None,
        captain_account_id: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        return await self.insert_returning_id(
            """
            INSERT INTO team
              (guild_channel_id, context, name, tag, captain_account_id, metadata)
            VALUES
              (%s, %s, %s, %s, %s, %s);
            """,
            (guild_channel_id, context, name, tag, captain_account_id, to_json(metadata)),
        )

    async def get_team_by_name(self, *, guild_channel_id: int, context: str, name: str) -> Mapping[str, Any] | None:
        return await self.fetch_one(
            """
            SELECT team_id, guild_channel_id, context, name, tag, captain_account_id, is_active, metadata
            FROM team
            WHERE guild_channel_id=%s AND context=%s AND name=%s;
            """,
            (guild_channel_id, context, name),
        )

    async def list_teams(self, *, guild_channel_id: int, context: str) -> list[Mapping[str, Any]]:
        return await self.fetch_all(
            """
            SELECT team_id, name, tag, captain_account_id, is_active, created_at
            FROM team
            WHERE guild_channel_id=%s AND context=%s
            ORDER BY created_at DESC;
            """,
            (guild_channel_id, context),
        )

    async def add_member(
        self,
        *,
        team_id: int,
        account_id: int,
        role: str = "starter",
        slot: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO team_member
              (team_id, account_id, role, slot, metadata)
            VALUES
              (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              role = VALUES(role),
              slot = VALUES(slot),
              metadata = COALESCE(VALUES(metadata), metadata);
            """,
            (team_id, account_id, role, slot, to_json(metadata)),
        )

    async def remove_member(self, *, team_id: int, account_id: int) -> int:
        return await self.execute(
            "DELETE FROM team_member WHERE team_id=%s AND account_id=%s;",
            (team_id, account_id),
        )

    async def get_roster(self, *, team_id: int) -> list[Mapping[str, Any]]:
        return await self.fetch_all(
            """
            SELECT
              tm.team_id,
              tm.account_id,
              tm.role,
              tm.slot,
              pa.display_name,
              pa.username
            FROM team_member tm
            JOIN platform_account pa ON pa.account_id = tm.account_id
            WHERE tm.team_id=%s
            ORDER BY
              CASE tm.role WHEN 'starter' THEN 0 ELSE 1 END,
              tm.slot IS NULL, tm.slot,
              pa.display_name;
            """,
            (team_id,),
        )

    async def set_captain(self, *, team_id: int, captain_account_id: int | None) -> int:
        return await self.execute(
            "UPDATE team SET captain_account_id=%s WHERE team_id=%s;",
            (captain_account_id, team_id),
        )
