# services/event_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

import random

from repositories.event_repo import EventRepo


class EventServiceError(Exception):
    pass


class EventNotFoundError(EventServiceError):
    pass


class EventStatusError(EventServiceError):
    pass


class EventCapacityError(EventServiceError):
    pass


class EventTeamBuildError(EventServiceError):
    pass


@dataclass(frozen=True)
class EventInfo:
    event_id: int
    name: str
    format: str
    team_size: int
    max_players: int
    status: str
    guild_channel_id: int
    announce_channel_id: Optional[int]


class EventService:
    """
    Event lifecycle + registration + team generation.

    Designed to keep DB clean:
      - event_registration is your canonical "who signed up"
      - event_team/event_team_member is generated when event is locked
      - bracket creation (event_match) can happen later (bracket_service)
    """

    def __init__(self, event_repo: EventRepo) -> None:
        self._repo = event_repo

    # -------------------------
    # Event lifecycle
    # -------------------------

    async def create_event(
        self,
        *,
        guild_channel_id: int,
        announce_channel_id: int | None,
        name: str,
        format: str = "double_elim",
        team_size: int = 2,
        max_players: int = 48,
        created_by_account_id: int | None = None,
        starts_at: datetime | None = None,
        rules_json: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        team_size = int(team_size)
        if team_size < 1 or team_size > 4:
            raise EventServiceError("team_size must be between 1 and 4")

        max_players = int(max_players)
        if max_players < team_size:
            raise EventServiceError("max_players must be >= team_size")

        fmt = (format or "double_elim").strip().lower()
        if fmt not in ("single_elim", "double_elim"):
            raise EventServiceError("format must be 'single_elim' or 'double_elim'")

        return await self._repo.create_event(
            guild_channel_id=guild_channel_id,
            announce_channel_id=announce_channel_id,
            name=name.strip(),
            format=fmt,
            team_size=team_size,
            max_players=max_players,
            created_by_account_id=created_by_account_id,
            starts_at=starts_at,
            rules_json=rules_json,
            metadata=metadata,
        )

    async def get_event_info(self, *, event_id: int) -> EventInfo:
        row = await self._repo.get_event(event_id=event_id)
        if not row:
            raise EventNotFoundError(f"Event not found: {event_id}")

        return EventInfo(
            event_id=int(row["event_id"]),
            name=str(row["name"]),
            format=str(row["format"]),
            team_size=int(row["team_size"]),
            max_players=int(row["max_players"]),
            status=str(row["status"]),
            guild_channel_id=int(row["guild_channel_id"]),
            announce_channel_id=int(row["announce_channel_id"]) if row.get("announce_channel_id") is not None else None,
        )

    async def set_status(self, *, event_id: int, status: str) -> None:
        status = (status or "").strip().lower()
        if status not in ("draft", "open", "locked", "active", "completed"):
            raise EventServiceError("Invalid status")
        changed = await self._repo.set_event_status(event_id=event_id, status=status)
        if changed == 0:
            raise EventNotFoundError(f"Event not found: {event_id}")

    async def open_event(self, *, event_id: int) -> None:
        info = await self.get_event_info(event_id=event_id)
        if info.status not in ("draft", "open"):
            raise EventStatusError(f"Cannot open event from status '{info.status}'")
        await self.set_status(event_id=event_id, status="open")

    # -------------------------
    # Registration
    # -------------------------

    async def register_player(self, *, event_id: int, account_id: int) -> None:
        info = await self.get_event_info(event_id=event_id)
        if info.status not in ("draft", "open"):
            raise EventStatusError(f"Registration is closed (status '{info.status}')")

        regs = await self._repo.list_registrations(event_id=event_id)
        active_count = sum(1 for r in regs if (r.get("status") or "").lower() == "active")

        # If already active, no-op
        already_active = any(int(r["account_id"]) == int(account_id) and (r.get("status") or "").lower() == "active" for r in regs)
        if already_active:
            return

        if active_count >= info.max_players:
            raise EventCapacityError(f"Event is full ({info.max_players} max players).")

        await self._repo.register_player(event_id=event_id, account_id=account_id, metadata={"source": "bot"})

    async def drop_player(self, *, event_id: int, account_id: int) -> None:
        info = await self.get_event_info(event_id=event_id)
        if info.status not in ("draft", "open"):
            raise EventStatusError(f"Cannot drop after lock (status '{info.status}')")

        await self._repo.drop_player(event_id=event_id, account_id=account_id)

    async def list_active_registrations(self, *, event_id: int) -> list[Mapping[str, Any]]:
        regs = await self._repo.list_registrations(event_id=event_id)
        return [r for r in regs if (r.get("status") or "").lower() == "active"]

    # -------------------------
    # Team generation (randomized)
    # -------------------------

    async def lock_and_generate_random_teams(
        self,
        *,
        event_id: int,
        seed: int | None = None,
        allow_incomplete_last_team: bool = False,
    ) -> list[int]:
        """
        Locks the event and creates event_team + event_team_member rows by randomizing registrations.
        Returns: list of created event_team_id in seed order.

        Rules:
          - team_size=1..4
          - By default requires registrations divisible by team_size (except team_size=1).
          - If allow_incomplete_last_team=True, last team may be smaller if remainder exists.
        """
        info = await self.get_event_info(event_id=event_id)

        if info.status not in ("draft", "open"):
            raise EventStatusError(f"Cannot lock/generate teams from status '{info.status}'")

        existing_teams = await self._repo.list_event_teams(event_id=event_id)
        if existing_teams:
            raise EventStatusError("Teams already exist for this event (lock/generate already performed).")

        regs = await self.list_active_registrations(event_id=event_id)
        accounts = [int(r["account_id"]) for r in regs]

        if len(accounts) < info.team_size:
            raise EventTeamBuildError(f"Not enough registrations to form a team (need {info.team_size}).")

        if info.team_size > 1:
            rem = len(accounts) % info.team_size
            if rem != 0 and not allow_incomplete_last_team:
                raise EventTeamBuildError(
                    f"Registered players ({len(accounts)}) must be divisible by team_size ({info.team_size}). "
                    f"Remainder={rem}. Add/remove players or enable allow_incomplete_last_team."
                )

        # deterministic randomization if seed provided
        rng = random.Random(seed if seed is not None else None)
        rng.shuffle(accounts)

        # build teams
        teams: list[list[int]] = []
        i = 0
        while i < len(accounts):
            chunk = accounts[i : i + info.team_size]
            if len(chunk) < info.team_size and not allow_incomplete_last_team:
                break
            teams.append(chunk)
            i += info.team_size

        if not teams:
            raise EventTeamBuildError("Failed to form any teams from registrations.")

        # write teams in order; seed starts at 1
        created_team_ids: list[int] = []
        for idx, members in enumerate(teams, start=1):
            # Optional: prettier default name
            display_name = f"Team {idx}"
            md = {"generated": True, "seed": idx}
            if seed is not None:
                md["rng_seed"] = seed
            if len(members) != info.team_size:
                md["incomplete"] = True
                md["expected_team_size"] = info.team_size
                md["actual_team_size"] = len(members)

            event_team_id = await self._repo.create_event_team(
                event_id=event_id,
                base_team_id=None,
                display_name=display_name,
                seed=idx,
                metadata=md,
            )
            created_team_ids.append(int(event_team_id))

            # add members (starters only for now; backups can be added later)
            for slot, account_id in enumerate(members, start=1):
                await self._repo.add_event_team_member(
                    event_team_id=event_team_id,
                    account_id=account_id,
                    role="starter",
                    slot=slot,
                    metadata={"source": "randomize"},
                )

        # Lock event
        await self.set_status(event_id=event_id, status="locked")
        return created_team_ids

    async def get_event_teams_with_rosters(self, *, event_id: int) -> list[dict[str, Any]]:
        teams = await self._repo.list_event_teams(event_id=event_id)
        out: list[dict[str, Any]] = []

        for t in teams:
            roster = await self._repo.get_event_team_roster(event_team_id=int(t["event_team_id"]))
            out.append(
                {
                    "event_team_id": int(t["event_team_id"]),
                    "seed": int(t["seed"]) if t.get("seed") is not None else None,
                    "display_name": t.get("display_name"),
                    "base_team_id": int(t["base_team_id"]) if t.get("base_team_id") is not None else None,
                    "roster": roster,
                }
            )

        # order by seed if present
        out.sort(key=lambda x: (x["seed"] is None, x["seed"] or 999999, x["event_team_id"]))
        return out
