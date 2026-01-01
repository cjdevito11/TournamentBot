# services/stats_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from db.tx import transaction
from repositories.event_repo import EventRepo
from repositories.stats_repo import StatsRepo
from services.bracket_service import BracketService


class StatsServiceError(Exception):
    pass


class MatchNotFoundError(StatsServiceError):
    pass


class MatchStateError(StatsServiceError):
    pass


class UnauthorizedStatError(StatsServiceError):
    pass


@dataclass(frozen=True)
class PlayerStatInput:
    account_id: int
    event_team_id: int
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    participated: bool = True
    metadata: Optional[Mapping[str, Any]] = None


class StatsService:
    """
    Orchestrates match reporting + per-player stats persistence + bracket advancement.

    Source of truth:
      - event_match: winner/loser
      - event_match_player_stat: kills/deaths/assists/participation per player per match

    This service does NOT format output; renderers do that.
    """

    def __init__(
        self,
        event_repo: EventRepo,
        stats_repo: StatsRepo,
        bracket_service: BracketService,
    ) -> None:
        self._event_repo = event_repo
        self._stats_repo = stats_repo
        self._brackets = bracket_service

    # -------------------------
    # Reporting
    # -------------------------

    async def report_match_by_code(
        self,
        *,
        event_id: int,
        match_code: str,
        winner_seed: int,
        reported_by_account_id: Optional[int] = None,
        player_stats: Sequence[PlayerStatInput] | None = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """
        Resolves (event_id + match_code) -> event_match_id
        Resolves (event_id + winner_seed) -> winner_event_team_id
        Then calls report_match(...) which remains the transactional source of truth.

        Returns event_id for convenience (same as report_match).
        """
        # match lookup (by event + bracket/round/match_no)
        from services.bracket_service import parse_match_code  # module-level helper in your bracket_service.py

        bracket, round_no, match_no = parse_match_code(match_code)

        m = await self._event_repo.fetch_one(
            """
            SELECT event_match_id, team1_event_team_id, team2_event_team_id
            FROM event_match
            WHERE event_id=%s AND bracket=%s AND round_no=%s AND match_no=%s
            LIMIT 1;
            """,
            (int(event_id), bracket, int(round_no), int(match_no)),
        )
        if not m:
            raise MatchNotFoundError(f"Match not found for event {event_id}: {match_code}")

        winner_row = await self._event_repo.fetch_one(
            "SELECT event_team_id FROM event_team WHERE event_id=%s AND seed=%s LIMIT 1;",
            (int(event_id), int(winner_seed)),
        )
        if not winner_row:
            raise MatchStateError(f"Winner seed {winner_seed} does not exist for event {event_id}.")

        return await self.report_match(
            event_match_id=int(m["event_match_id"]),
            winner_event_team_id=int(winner_row["event_team_id"]),
            reported_by_account_id=reported_by_account_id,
            player_stats=player_stats,
            metadata=metadata,
        )

    async def report_match(
        self,
        *,
        event_match_id: int,
        winner_event_team_id: int,
        reported_by_account_id: Optional[int] = None,
        player_stats: Sequence[PlayerStatInput] | None = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """
        Atomically:
          1) Completes the match (winner/loser)
          2) Upserts provided player stats lines
          3) Advances bracket (creates next matches if ready)

        Returns the event_id for convenience.
        """
        m = await self._event_repo.fetch_one(
            "SELECT * FROM event_match WHERE event_match_id=%s;",
            (event_match_id,),
        )
        if not m:
            raise MatchNotFoundError("Match not found.")

        status = str(m.get("status") or "").lower()
        if status == "completed":
            # Idempotent: still attempt to advance bracket in case prior run died mid-way
            await self._brackets.advance(event_id=int(m["event_id"]))
            return int(m["event_id"])

        t1 = int(m["team1_event_team_id"])
        t2 = int(m["team2_event_team_id"]) if m.get("team2_event_team_id") is not None else None
        if t2 is None:
            raise MatchStateError("This match is a BYE and should not be manually reported.")

        w = int(winner_event_team_id)
        if w not in (t1, t2):
            raise MatchStateError("winner_event_team_id must be team1_event_team_id or team2_event_team_id.")
        loser = t2 if w == t1 else t1
        event_id = int(m["event_id"])

        # Validate player stat lines (optional)
        lines = list(player_stats or [])
        for line in lines:
            if int(line.event_team_id) not in (t1, t2):
                raise UnauthorizedStatError("Player stat line references a team not in this match.")

            ok = await self._event_repo.fetch_one(
                """
                SELECT 1
                FROM event_team_member
                WHERE event_team_id=%s AND account_id=%s
                LIMIT 1;
                """,
                (int(line.event_team_id), int(line.account_id)),
            )
            if not ok:
                raise UnauthorizedStatError("Player stat line includes a player not on that event team.")

        # Transaction: complete match + upsert stats
        async with transaction(self._event_repo.pool, dict_rows=False) as (_conn, cur):
            await cur.execute(
                """
                UPDATE event_match
                SET
                  status='completed',
                  winner_event_team_id=%s,
                  loser_event_team_id=%s,
                  reported_by_account_id=%s,
                  reported_at=NOW(6),
                  metadata=COALESCE(%s, metadata),
                  updated_at=NOW(6)
                WHERE event_match_id=%s
                  AND status <> 'completed';
                """,
                (
                    w,
                    loser,
                    reported_by_account_id,
                    (None if metadata is None else __import__("json").dumps(metadata, separators=(",", ":"), ensure_ascii=False)),
                    event_match_id,
                ),
            )

            # Upsert per-player stats (if provided)
            if lines:
                for s in lines:
                    await cur.execute(
                        """
                        INSERT INTO event_match_player_stat
                          (event_match_id, account_id, event_team_id, kills, deaths, assists, is_participated, metadata)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                          event_team_id   = VALUES(event_team_id),
                          kills           = VALUES(kills),
                          deaths          = VALUES(deaths),
                          assists         = VALUES(assists),
                          is_participated = VALUES(is_participated),
                          metadata        = COALESCE(VALUES(metadata), metadata);
                        """,
                        (
                            event_match_id,
                            int(s.account_id),
                            int(s.event_team_id),
                            max(0, int(s.kills)),
                            max(0, int(s.deaths)),
                            max(0, int(s.assists)),
                            1 if bool(s.participated) else 0,
                            (None if s.metadata is None else __import__("json").dumps(s.metadata, separators=(",", ":"), ensure_ascii=False)),
                        ),
                    )

        # Advance bracket after commit
        await self._brackets.advance(event_id=event_id)

        # Optional: keep event status sane (locked->active, completed when finals done)
        await self._maybe_update_event_status(event_id)

        return event_id

    # -------------------------
    # Leaderboards / rollups
    # -------------------------

    async def get_player_leaderboard(self, *, event_id: int) -> list[Mapping[str, Any]]:
        """
        Returns aggregated per-player totals for an event.
        Sorted in repo query: wins desc, kills desc, deaths asc.
        """
        return await self._stats_repo.event_player_totals(event_id=event_id)

    async def get_team_records(self, *, event_id: int) -> list[Mapping[str, Any]]:
        """
        Returns W/L per event_team for an event.
        """
        return await self._stats_repo.event_team_records(event_id=event_id)

    # -------------------------
    # Internal event status helpers
    # -------------------------

    async def _maybe_update_event_status(self, event_id: int) -> None:
        ev = await self._event_repo.get_event(event_id=event_id)
        if not ev:
            return

        fmt = str(ev.get("format") or "").lower()
        status = str(ev.get("status") or "").lower()

        matches = await self._event_repo.list_matches(event_id=event_id)
        any_completed = any(str(m.get("status") or "").lower() == "completed" for m in matches)

        if any_completed and status in ("draft", "open", "locked"):
            await self._event_repo.set_event_status(event_id=event_id, status="active")
            status = "active"

        is_complete = False

        if fmt == "single_elim":
            wb = [m for m in matches if str(m.get("bracket")) == "W"]
            if wb:
                max_r = max(int(m["round_no"]) for m in wb)
                finals = [m for m in wb if int(m["round_no"]) == max_r]
                if len(finals) == 1 and str(finals[0].get("status") or "").lower() == "completed":
                    is_complete = True

        elif fmt == "double_elim":
            gf = [m for m in matches if str(m.get("bracket")) == "GF" and int(m.get("round_no") or 0) == 1]
            gf1 = next((m for m in gf if int(m.get("match_no") or 0) == 1), None)
            gf2 = next((m for m in gf if int(m.get("match_no") or 0) == 2), None)

            if gf2 is not None:
                is_complete = str(gf2.get("status") or "").lower() == "completed"
            elif gf1 is not None:
                is_complete = str(gf1.get("status") or "").lower() == "completed"

        if is_complete and status != "completed":
            async with transaction(self._event_repo.pool, dict_rows=False) as (_conn, cur):
                await cur.execute(
                    "UPDATE event SET status='completed', ended_at=NOW(6), updated_at=NOW(6) WHERE event_id=%s;",
                    (event_id,),
                )
