# services/team_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from repositories.team_repo import TeamRepo


class TeamServiceError(Exception):
    pass


class TeamNotFoundError(TeamServiceError):
    pass


class TeamCapacityError(TeamServiceError):
    pass


class TeamNameConflictError(TeamServiceError):
    pass


@dataclass(frozen=True)
class TeamRosterMember:
    account_id: int
    display_name: str
    role: str  # starter|backup
    slot: Optional[int]


@dataclass(frozen=True)
class TeamRoster:
    team_id: int
    name: str
    tag: Optional[str]
    captain_account_id: Optional[int]
    starters: list[TeamRosterMember]
    backups: list[TeamRosterMember]


class TeamService:
    """
    Business rules for persistent squads/teams.

    - Contexts:
        * ladder_reset: long-lived squads
        * event: reusable squads for events (optional)
    - Enforces:
        * max starters = team_size (default 2, used for ladder squads)
        * max backups = backup_limit (default 2, configurable)
        * roster ordering via optional slot
    """

    def __init__(
        self,
        team_repo: TeamRepo,
        *,
        default_team_size: int = 2,
        default_backup_limit: int = 2,
    ) -> None:
        self._repo = team_repo
        self._default_team_size = int(default_team_size)
        self._default_backup_limit = int(default_backup_limit)

        if self._default_team_size < 1 or self._default_team_size > 4:
            raise ValueError("default_team_size must be between 1 and 4")
        if self._default_backup_limit < 0:
            raise ValueError("default_backup_limit must be >= 0")

    # -------------------------
    # Core helpers
    # -------------------------

    async def get_team_by_name(self, *, guild_channel_id: int, context: str, name: str) -> Mapping[str, Any]:
        team = await self._repo.get_team_by_name(guild_channel_id=guild_channel_id, context=context, name=name)
        if not team:
            raise TeamNotFoundError(f"Team not found: {name}")
        return team

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
        existing = await self._repo.get_team_by_name(guild_channel_id=guild_channel_id, context=context, name=name)
        if existing:
            raise TeamNameConflictError(f"A team named '{name}' already exists in this context.")

        return await self._repo.create_team(
            guild_channel_id=guild_channel_id,
            context=context,
            name=name,
            tag=tag,
            captain_account_id=captain_account_id,
            metadata=metadata,
        )

    async def set_captain(self, *, team_id: int, captain_account_id: int | None) -> None:
        await self._repo.set_captain(team_id=team_id, captain_account_id=captain_account_id)

    async def list_teams(self, *, guild_channel_id: int, context: str) -> list[Mapping[str, Any]]:
        return await self._repo.list_teams(guild_channel_id=guild_channel_id, context=context)

    # -------------------------
    # Membership logic
    # -------------------------

    async def join_team(
        self,
        *,
        team_id: int,
        account_id: int,
        role: str = "starter",
        slot: int | None = None,
        team_size: int | None = None,
        backup_limit: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """
        Join or update membership role (starter/backup).
        Enforces team_size and backup_limit.
        """
        role = (role or "starter").lower().strip()
        if role not in ("starter", "backup"):
            raise TeamServiceError("role must be 'starter' or 'backup'")

        if slot is not None:
            slot = int(slot)
            if slot < 1 or slot > 4:
                raise TeamServiceError("slot must be between 1 and 4")

        team_size = int(team_size or self._default_team_size)
        backup_limit = int(backup_limit if backup_limit is not None else self._default_backup_limit)

        if team_size < 1 or team_size > 4:
            raise TeamServiceError("team_size must be between 1 and 4")
        if backup_limit < 0:
            raise TeamServiceError("backup_limit must be >= 0")

        roster = await self._repo.get_roster(team_id=team_id)

        starters = [r for r in roster if (r.get("role") or "").lower() == "starter"]
        backups = [r for r in roster if (r.get("role") or "").lower() == "backup"]

        # If they are already on the roster, allow role changes if capacity allows.
        already = next((r for r in roster if int(r["account_id"]) == int(account_id)), None)

        # Capacity checks only apply when adding/promoting into that bucket
        if role == "starter":
            starters_count = len(starters)
            if already and (already.get("role") or "").lower() == "starter":
                # no capacity change
                pass
            else:
                if starters_count >= team_size:
                    raise TeamCapacityError(f"Starter slots are full ({team_size}). Join as backup or remove a starter.")

        if role == "backup":
            backups_count = len(backups)
            if backup_limit == 0:
                raise TeamCapacityError("Backups are disabled for this team.")
            if already and (already.get("role") or "").lower() == "backup":
                pass
            else:
                if backups_count >= backup_limit:
                    raise TeamCapacityError(f"Backup slots are full ({backup_limit}).")

        await self._repo.add_member(
            team_id=team_id,
            account_id=account_id,
            role=role,
            slot=slot,
            metadata=metadata,
        )

    async def leave_team(self, *, team_id: int, account_id: int) -> None:
        deleted = await self._repo.remove_member(team_id=team_id, account_id=account_id)
        if deleted == 0:
            raise TeamServiceError("You are not on that team.")

    async def get_roster(
        self,
        *,
        team_id: int,
        team_name: str | None = None,
        tag: str | None = None,
        captain_account_id: int | None = None,
    ) -> TeamRoster:
        roster_rows = await self._repo.get_roster(team_id=team_id)

        starters: list[TeamRosterMember] = []
        backups: list[TeamRosterMember] = []

        for r in roster_rows:
            member = TeamRosterMember(
                account_id=int(r["account_id"]),
                display_name=str(r.get("display_name") or r.get("username") or r["account_id"]),
                role=str(r.get("role") or "starter").lower(),
                slot=int(r["slot"]) if r.get("slot") is not None else None,
            )
            if member.role == "backup":
                backups.append(member)
            else:
                starters.append(member)

        return TeamRoster(
            team_id=int(team_id),
            name=team_name or f"Team {team_id}",
            tag=tag,
            captain_account_id=captain_account_id,
            starters=starters,
            backups=backups,
        )

    async def get_roster_by_name(
        self,
        *,
        guild_channel_id: int,
        context: str,
        name: str,
    ) -> TeamRoster:
        team = await self.get_team_by_name(guild_channel_id=guild_channel_id, context=context, name=name)
        return await self.get_roster(
            team_id=int(team["team_id"]),
            team_name=str(team["name"]),
            tag=team.get("tag"),
            captain_account_id=int(team["captain_account_id"]) if team.get("captain_account_id") is not None else None,
        )

    # -------------------------
    # Convenience utilities
    # -------------------------

    async def ensure_team(
        self,
        *,
        guild_channel_id: int,
        context: str,
        name: str,
        tag: str | None = None,
        captain_account_id: int | None = None,
    ) -> int:
        """
        Get or create a team by name.
        """
        existing = await self._repo.get_team_by_name(guild_channel_id=guild_channel_id, context=context, name=name)
        if existing:
            return int(existing["team_id"])
        return await self._repo.create_team(
            guild_channel_id=guild_channel_id,
            context=context,
            name=name,
            tag=tag,
            captain_account_id=captain_account_id,
            metadata={"source": "bot"},
        )
