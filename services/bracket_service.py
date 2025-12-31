# services/bracket_service.py
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

import aiomysql

from repositories.event_repo import EventRepo


class BracketServiceError(Exception):
    pass


class BracketAlreadyExistsError(BracketServiceError):
    pass


class BracketStateError(BracketServiceError):
    pass


@dataclass(frozen=True)
class MatchRef:
    event_match_id: int
    bracket: str   # W|L|GF
    round_no: int
    match_no: int
    team1_event_team_id: int
    team2_event_team_id: Optional[int]
    status: str
    winner_event_team_id: Optional[int]
    loser_event_team_id: Optional[int]


def _next_pow2(n: int) -> int:
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def _pair_seeded_round1(team_ids_sorted_by_seed: list[int], bracket_size: int) -> list[tuple[int, Optional[int]]]:
    """
    Simple seeding: 1 vs N, 2 vs N-1, etc (after padding with BYEs).
    """
    padded: list[Optional[int]] = list(team_ids_sorted_by_seed)
    while len(padded) < bracket_size:
        padded.append(None)

    pairs: list[tuple[int, Optional[int]]] = []
    half = bracket_size // 2
    for i in range(half):
        t1 = padded[i]
        t2 = padded[bracket_size - 1 - i]
        if t1 is None:
            # if we ever hit a None on the left, bracket creation is invalid
            raise BracketStateError("Invalid seeding/padding state (missing team on left side).")
        pairs.append((t1, t2))
    return pairs


class BracketService:
    """
    Responsible for:
      - Creating initial matches from event_team seeds
      - Advancing brackets when matches complete (single and double elim)

    Notes:
      - We store bracket rounds entirely in event_match rows.
      - BYEs are represented as matches with team2_event_team_id = NULL and auto-completed.
    """

    def __init__(self, event_repo: EventRepo) -> None:
        self._repo = event_repo

    # -------------------------
    # Public API
    # -------------------------

    async def create_bracket(self, *, event_id: int) -> None:
        """
        Create the initial bracket matches based on event.format.
        Requires:
          - event_team exists (generated/seeded)
          - no event_match exists yet for this event
        """
        existing = await self._repo.list_matches(event_id=event_id)
        if existing:
            raise BracketAlreadyExistsError("Matches already exist for this event.")

        event = await self._repo.get_event(event_id=event_id)
        if not event:
            raise BracketStateError("Event not found.")
        fmt = str(event["format"]).lower()

        teams = await self._repo.list_event_teams(event_id=event_id)
        if not teams:
            raise BracketStateError("No event teams found. Generate/lock teams first.")

        # sort by seed then id
        teams_sorted = sorted(
            teams,
            key=lambda t: (
                t.get("seed") is None,
                int(t.get("seed") or 999999),
                int(t["event_team_id"]),
            ),
        )
        team_ids = [int(t["event_team_id"]) for t in teams_sorted]

        bracket_size = _next_pow2(len(team_ids))
        pairs = _pair_seeded_round1(team_ids, bracket_size)

        # Create Winners Bracket Round 1
        for match_no, (t1, t2) in enumerate(pairs, start=1):
            match_id = await self._repo.create_match(
                event_id=event_id,
                bracket="W",
                round_no=1,
                match_no=match_no,
                team1_event_team_id=t1,
                team2_event_team_id=t2,
                metadata={"generated": True, "bracket_size": bracket_size},
            )
            if t2 is None:
                await self._set_bye_winner(event_match_id=match_id, winner_event_team_id=t1)

        # Try to auto-advance through any BYE-only rounds
        await self.advance(event_id=event_id)

        # For double elimination, LB is created as results become available
        if fmt not in ("single_elim", "double_elim"):
            raise BracketStateError("Unsupported event format in DB (expected single_elim or double_elim).")

    async def record_result(
        self,
        *,
        event_match_id: int,
        winner_event_team_id: int,
        reported_by_account_id: Optional[int] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Record a match result (non-BYE) and advance bracket accordingly.
        """
        m = await self._repo.fetch_one("SELECT * FROM event_match WHERE event_match_id=%s;", (event_match_id,))
        if not m:
            raise BracketStateError("Match not found.")

        if (m.get("status") or "").lower() == "completed":
            return

        t1 = int(m["team1_event_team_id"])
        t2 = int(m["team2_event_team_id"]) if m.get("team2_event_team_id") is not None else None
        if t2 is None:
            # BYE match should be auto-completed
            await self._set_bye_winner(event_match_id=event_match_id, winner_event_team_id=t1)
            return

        w = int(winner_event_team_id)
        if w not in (t1, t2):
            raise BracketStateError("winner_event_team_id must be team1 or team2 for this match.")

        loser = t2 if w == t1 else t1

        await self._repo.set_match_result(
            event_match_id=event_match_id,
            winner_event_team_id=w,
            loser_event_team_id=loser,
            reported_by_account_id=reported_by_account_id,
            metadata=dict(metadata) if metadata else None,
        )

        await self.advance(event_id=int(m["event_id"]))

    async def advance(self, *, event_id: int) -> None:
        """
        Advances bracket by creating next matches when prior rounds are complete.
        """
        event = await self._repo.get_event(event_id=event_id)
        if not event:
            raise BracketStateError("Event not found.")
        fmt = str(event["format"]).lower()

        if fmt == "single_elim":
            await self._advance_single_elim(event_id)
        elif fmt == "double_elim":
            await self._advance_double_elim(event_id)
        else:
            raise BracketStateError("Unsupported event format.")

    async def get_bracket_matches(self, *, event_id: int) -> list[MatchRef]:
        rows = await self._repo.list_matches(event_id=event_id)
        out: list[MatchRef] = []
        for r in rows:
            out.append(
                MatchRef(
                    event_match_id=int(r["event_match_id"]),
                    bracket=str(r["bracket"]),
                    round_no=int(r["round_no"]),
                    match_no=int(r["match_no"]),
                    team1_event_team_id=int(r["team1_event_team_id"]),
                    team2_event_team_id=int(r["team2_event_team_id"]) if r.get("team2_event_team_id") is not None else None,
                    status=str(r["status"]),
                    winner_event_team_id=int(r["winner_event_team_id"]) if r.get("winner_event_team_id") is not None else None,
                    loser_event_team_id=int(r["loser_event_team_id"]) if r.get("loser_event_team_id") is not None else None,
                )
            )
        return out

    # -------------------------
    # Internals
    # -------------------------

    async def _set_bye_winner(self, *, event_match_id: int, winner_event_team_id: int) -> None:
        """
        Marks a BYE match as completed with winner set, loser NULL.
        Uses direct SQL to avoid forcing repo signature changes.
        """
        await self._repo.execute(
            """
            UPDATE event_match
            SET status='completed',
                winner_event_team_id=%s,
                loser_event_team_id=NULL,
                reported_at=NOW(6),
                updated_at=NOW(6),
                metadata=JSON_MERGE_PATCH(COALESCE(metadata, JSON_OBJECT()), JSON_OBJECT('bye', true))
            WHERE event_match_id=%s;
            """,
            (winner_event_team_id, event_match_id),
        )

    def _group(self, matches: list[Mapping[str, Any]], bracket: str, round_no: int) -> list[Mapping[str, Any]]:
        ms = [m for m in matches if str(m["bracket"]) == bracket and int(m["round_no"]) == int(round_no)]
        ms.sort(key=lambda x: int(x["match_no"]))
        return ms

    def _all_completed(self, ms: list[Mapping[str, Any]]) -> bool:
        return bool(ms) and all(str(m["status"]).lower() == "completed" for m in ms)

    def _winners_in_order(self, ms: list[Mapping[str, Any]]) -> list[int]:
        winners: list[int] = []
        for m in ms:
            w = m.get("winner_event_team_id")
            if w is None:
                # should not happen if completed, but guard anyway
                t1 = int(m["team1_event_team_id"])
                t2 = int(m["team2_event_team_id"]) if m.get("team2_event_team_id") is not None else None
                winners.append(t1 if t2 is None else t1)
            else:
                winners.append(int(w))
        return winners

    def _losers_in_order(self, ms: list[Mapping[str, Any]]) -> list[int]:
        losers: list[int] = []
        for m in ms:
            # only include real losses (non-bye)
            if m.get("team2_event_team_id") is None:
                continue
            l = m.get("loser_event_team_id")
            if l is not None:
                losers.append(int(l))
        return losers

    async def _safe_create_match(
        self,
        *,
        event_id: int,
        bracket: str,
        round_no: int,
        match_no: int,
        t1: int,
        t2: Optional[int],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[int]:
        try:
            match_id = await self._repo.create_match(
                event_id=event_id,
                bracket=bracket,
                round_no=round_no,
                match_no=match_no,
                team1_event_team_id=t1,
                team2_event_team_id=t2,
                metadata=dict(metadata) if metadata else None,
            )
            if t2 is None:
                await self._set_bye_winner(event_match_id=match_id, winner_event_team_id=t1)
            return match_id
        except aiomysql.IntegrityError:
            # Unique key hit: someone else created it concurrently; ignore.
            return None

    async def _advance_single_elim(self, event_id: int) -> None:
        matches = await self._repo.list_matches(event_id=event_id)

        # Determine highest winners round that exists
        wb_rounds = sorted({int(m["round_no"]) for m in matches if str(m["bracket"]) == "W"})
        if not wb_rounds:
            return

        # Iteratively generate next round as long as current is completed
        r = 1
        while True:
            curr = self._group(matches, "W", r)
            if not curr or not self._all_completed(curr):
                break

            winners = self._winners_in_order(curr)
            if len(winners) <= 1:
                break  # champion reached

            next_round = r + 1
            if self._group(matches, "W", next_round):
                r = next_round
                continue  # already exists

            # create next round matches
            match_no = 1
            i = 0
            while i < len(winners):
                t1 = winners[i]
                t2 = winners[i + 1] if i + 1 < len(winners) else None
                await self._safe_create_match(
                    event_id=event_id,
                    bracket="W",
                    round_no=next_round,
                    match_no=match_no,
                    t1=t1,
                    t2=t2,
                    metadata={"generated": True, "from_round": r},
                )
                match_no += 1
                i += 2

            matches = await self._repo.list_matches(event_id=event_id)
            r = next_round

    async def _advance_double_elim(self, event_id: int) -> None:
        """
        Practical double elim:
          - WB advances like single elim
          - LB rounds generated using:
              LB1  from WB1 losers
              LB2  winners(LB1) vs WB2 losers
              LB3  winners(LB2)
              LB4  winners(LB3) vs WB3 losers
              ...
              LB(2n-2) winners(LB(2n-3)) vs WB-final loser
          - GF between WB champ and LB champ; if LB wins GF1, create GF2 reset.
        """
        matches = await self._repo.list_matches(event_id=event_id)

        # Always advance WB as far as possible first
        await self._advance_single_elim(event_id=event_id)
        matches = await self._repo.list_matches(event_id=event_id)

        wb_r1 = self._group(matches, "W", 1)
        if not wb_r1:
            return
        bracket_size = 2 * len(wb_r1)
        n = int(math.log2(bracket_size)) if bracket_size > 0 else 0

        # Helper to find if a bracket round exists
        def has_round(br: str, rn: int) -> bool:
            return bool(self._group(matches, br, rn))

        # Build LB rounds in order as prerequisites become available
        # LB round 1
        if self._all_completed(wb_r1) and not has_round("L", 1):
            losers = self._losers_in_order(wb_r1)
            await self._create_round_from_pairs(event_id, "L", 1, losers, metadata={"generated": True, "source": "WB1"})
            matches = await self._repo.list_matches(event_id=event_id)

        # For WB rounds 2..n-1 build alternating LB rounds (even cross, odd pure)
        for wb_round in range(2, max(2, n)):  # up to n-1 inclusive
            wb = self._group(matches, "W", wb_round)
            if not wb or not self._all_completed(wb):
                break

            # Ensure prior LB chain progressed
            # Cross LB round = 2*wb_round - 2
            lb_cross = 2 * wb_round - 2
            lb_prev = lb_cross - 1  # pure/initial before cross

            if not has_round("L", lb_prev):
                break
            lb_prev_matches = self._group(matches, "L", lb_prev)
            if not self._all_completed(lb_prev_matches):
                break

            if not has_round("L", lb_cross):
                lb_winners = self._winners_in_order(lb_prev_matches)
                wb_losers = self._losers_in_order(wb)
                entrants = self._zip_cross(lb_winners, wb_losers)
                await self._create_round_from_cross(event_id, lb_cross, entrants, metadata={"generated": True, "source": f"WB{wb_round}"})
                matches = await self._repo.list_matches(event_id=event_id)

            # Pure LB round after cross = 2*wb_round - 1
            lb_pure = lb_cross + 1
            lb_cross_matches = self._group(matches, "L", lb_cross)
            if self._all_completed(lb_cross_matches) and not has_round("L", lb_pure):
                lb_winners2 = self._winners_in_order(lb_cross_matches)
                await self._create_round_from_pairs(event_id, "L", lb_pure, lb_winners2, metadata={"generated": True, "source": f"L{lb_cross}"})
                matches = await self._repo.list_matches(event_id=event_id)

        # Handle WB final -> LB final cross -> GF
        # WB final is round n (if it exists)
        wb_final = self._group(matches, "W", n)
        if not wb_final or not self._all_completed(wb_final):
            return

        wb_champ = self._winners_in_order(wb_final)[0]
        wb_final_losers = self._losers_in_order(wb_final)
        wb_final_loser = wb_final_losers[0] if wb_final_losers else None

        # LB round 2n-2 (final cross) requires LB round 2n-3 completed
        lb_last_pure = 2 * n - 3
        lb_last_cross = 2 * n - 2

        if not has_round("L", lb_last_pure):
            return
        lb_last_pure_matches = self._group(matches, "L", lb_last_pure)
        if not self._all_completed(lb_last_pure_matches):
            return
        lb_last_pure_winner = self._winners_in_order(lb_last_pure_matches)[0]

        if wb_final_loser is None:
            # can happen with extreme BYE scenarios; treat LB winner as champ contender
            wb_final_loser = lb_last_pure_winner

        if not has_round("L", lb_last_cross):
            # LB champ match: LB winner vs WB final loser
            await self._safe_create_match(
                event_id=event_id,
                bracket="L",
                round_no=lb_last_cross,
                match_no=1,
                t1=lb_last_pure_winner,
                t2=wb_final_loser,
                metadata={"generated": True, "source": f"WB{n}"},
            )
            matches = await self._repo.list_matches(event_id=event_id)

        lb_final = self._group(matches, "L", lb_last_cross)
        if not self._all_completed(lb_final):
            return
        lb_champ = self._winners_in_order(lb_final)[0]

        # Create GF match if not exists
        if not has_round("GF", 1):
            await self._safe_create_match(
                event_id=event_id,
                bracket="GF",
                round_no=1,
                match_no=1,
                t1=wb_champ,
                t2=lb_champ,
                metadata={"generated": True, "wb_champ": wb_champ, "lb_champ": lb_champ},
            )
            matches = await self._repo.list_matches(event_id=event_id)

        # If GF1 completed and LB champ won, create reset GF2 if not exists
        gf_round = self._group(matches, "GF", 1)
        gf1 = next((m for m in gf_round if int(m["match_no"]) == 1), None)
        gf2 = next((m for m in gf_round if int(m["match_no"]) == 2), None)

        if gf1 and str(gf1["status"]).lower() == "completed":
            gf1_winner = int(gf1["winner_event_team_id"]) if gf1.get("winner_event_team_id") is not None else None
            if gf1_winner == lb_champ and gf2 is None:
                await self._safe_create_match(
                    event_id=event_id,
                    bracket="GF",
                    round_no=1,
                    match_no=2,
                    t1=wb_champ,
                    t2=lb_champ,
                    metadata={"generated": True, "reset": True},
                )

    async def _create_round_from_pairs(
        self,
        event_id: int,
        bracket: str,
        round_no: int,
        entrants: list[int],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        entrants = list(entrants)
        if not entrants:
            return
        match_no = 1
        i = 0
        while i < len(entrants):
            t1 = entrants[i]
            t2 = entrants[i + 1] if i + 1 < len(entrants) else None
            await self._safe_create_match(
                event_id=event_id,
                bracket=bracket,
                round_no=round_no,
                match_no=match_no,
                t1=t1,
                t2=t2,
                metadata=metadata,
            )
            match_no += 1
            i += 2

    def _zip_cross(self, left: list[int], right: list[int]) -> list[tuple[int, Optional[int]]]:
        """
        Cross-round pairing: pair left[i] vs right[i]. If mismatch, pad with BYEs.
        """
        out: list[tuple[int, Optional[int]]] = []
        m = max(len(left), len(right))
        for i in range(m):
            t1 = left[i] if i < len(left) else None
            t2 = right[i] if i < len(right) else None
            if t1 is None and t2 is None:
                continue
            if t1 is None:
                # swap so t1 is never None
                t1, t2 = t2, None
            out.append((t1, t2))
        return out

    async def _create_round_from_cross(
        self,
        event_id: int,
        round_no: int,
        entrants: list[tuple[int, Optional[int]]],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        match_no = 1
        for (t1, t2) in entrants:
            await self._safe_create_match(
                event_id=event_id,
                bracket="L",
                round_no=round_no,
                match_no=match_no,
                t1=t1,
                t2=t2,
                metadata=metadata,
            )
            match_no += 1
