# services/bracket_service.py
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Tuple

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


MATCH_CODE_RE = re.compile(r"^(?:(GF)|([WL])(\d+))-(\d+)$", re.IGNORECASE)


def next_power_of_two(n: int) -> int:
    if n <= 1:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def seeded_positions(n: int) -> list[int]:
    """
    Standard tournament seed positions list (length n, n is power of two).
    Example n=8 => [1,8,4,5,2,7,3,6]
    """
    if n <= 1:
        return [1]
    if n == 2:
        return [1, 2]
    prev = seeded_positions(n // 2)
    out: list[int] = []
    for s in prev:
        out.append(s)
        out.append(n + 1 - s)
    return out


def parse_match_code(code: str) -> tuple[str, int, int]:
    """
    Accepts:
      - W1-01, W2-1, L3-04
      - GF-01, gf-2
    Returns: (bracket, round_no, match_no)
    """
    c = (code or "").strip().upper()
    m = MATCH_CODE_RE.match(c)
    if not m:
        raise BracketStateError("Invalid match_code. Use W1-01, L2-03, or GF-01 format.")

    gf, wl, round_s, match_s = m.groups()

    match_no = int(match_s)
    if match_no < 1:
        raise BracketStateError("match_no in match_code must be >= 1.")

    if gf:
        return ("GF", 1, match_no)

    bracket = wl.upper()
    round_no = int(round_s)
    if round_no < 1:
        raise BracketStateError("round_no in match_code must be >= 1.")

    return (bracket, round_no, match_no)


def _validate_seeds(teams: list[Mapping[str, Any]]) -> dict[int, int]:
    """
    Validates event_team seeds are 1..N with no gaps and unique.
    Returns seed -> event_team_id mapping.
    """
    seeds: list[int] = []
    for t in teams:
        s = t.get("seed")
        if s is None:
            raise BracketStateError("All event teams must have a seed before creating a bracket.")
        seeds.append(int(s))

    if len(set(seeds)) != len(seeds):
        raise BracketStateError("Duplicate seeds detected in event_team. Seeds must be unique.")

    n = len(seeds)
    if min(seeds) != 1 or max(seeds) != n:
        raise BracketStateError("Seeds must be contiguous starting at 1 (1..N).")

    seed_to_id: dict[int, int] = {}
    for t in teams:
        seed_to_id[int(t["seed"])] = int(t["event_team_id"])
    return seed_to_id


def _pair_round1_by_standard_seeding(seed_to_id: dict[int, int], team_count: int, bracket_size: int) -> list[tuple[int, Optional[int], int, Optional[int]]]:
    """
    Uses standard seed placement order. BYEs are any seed > team_count.
    Returns list of tuples:
      (team1_event_team_id, team2_event_team_id_or_none, seed1, seed2_or_none)
    """
    pos = seeded_positions(bracket_size)  # seeds 1..bracket_size in seeded order
    out: list[tuple[int, Optional[int], int, Optional[int]]] = []

    for i in range(0, bracket_size, 2):
        seed1 = pos[i]
        seed2 = pos[i + 1]

        t1 = seed_to_id.get(seed1) if seed1 <= team_count else None
        t2 = seed_to_id.get(seed2) if seed2 <= team_count else None

        if t1 is None:
            # should never happen if seeds are 1..N and seed1 is always a valid low seed
            raise BracketStateError("Invalid seeding state: missing team for a required seed.")

        out.append((t1, t2, seed1, seed2 if t2 is not None else None))

    return out


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

        # sort by seed then id (for validation + deterministic mapping)
        teams_sorted = sorted(
            teams,
            key=lambda t: (
                t.get("seed") is None,
                int(t.get("seed") or 999999),
                int(t["event_team_id"]),
            ),
        )

        seed_to_id = _validate_seeds(teams_sorted)
        team_count = len(seed_to_id)

        bracket_size = next_power_of_two(team_count)
        pairs = _pair_round1_by_standard_seeding(seed_to_id, team_count, bracket_size)

        # Create Winners Bracket Round 1 (W1)
        for match_no, (t1, t2, seed1, seed2) in enumerate(pairs, start=1):
            bracket = "W"
            round_no = 1
            code = f"W{round_no}-{match_no:02d}"

            match_id = await self._repo.create_match(
                event_id=event_id,
                bracket=bracket,
                round_no=round_no,
                match_no=match_no,
                team1_event_team_id=t1,
                team2_event_team_id=t2,
                metadata={
                    "generated": True,
                    "bracket_size": bracket_size,
                    "code": code,
                    "seed1": seed1,
                    "seed2": seed2,
                },
            )
            if t2 is None:
                await self._set_bye_winner(event_match_id=match_id, winner_event_team_id=t1)

        # Auto-advance through any BYE-only rounds
        await self.advance(event_id=event_id)

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
        m = await self._repo.fetch_one("SELECT * FROM event_match WHERE event_match_id=%s;", (event_match_id,))
        if not m:
            raise BracketStateError("Match not found.")

        if (m.get("status") or "").lower() == "completed":
            return

        t1 = int(m["team1_event_team_id"])
        t2 = int(m["team2_event_team_id"]) if m.get("team2_event_team_id") is not None else None
        if t2 is None:
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

    async def record_result_by_code(
        self,
        *,
        event_id: int,
        match_code: str,
        winner_seed: int,
        reported_by_account_id: Optional[int] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """
        Human-friendly reporting:
          match_code: W1-01, L2-03, GF-01
          winner_seed: 1..N (seed of the winning event_team)
        Returns the resolved event_match_id.
        """
        bracket, round_no, match_no = parse_match_code(match_code)

        m = await self._repo.fetch_one(
            """
            SELECT *
            FROM event_match
            WHERE event_id=%s AND bracket=%s AND round_no=%s AND match_no=%s;
            """,
            (event_id, bracket, round_no, match_no),
        )
        if not m:
            raise BracketStateError(f"Match not found for event {event_id}: {bracket}{round_no}-{match_no:02d}")

        row = await self._repo.fetch_one(
            "SELECT event_team_id FROM event_team WHERE event_id=%s AND seed=%s;",
            (event_id, int(winner_seed)),
        )
        if not row:
            raise BracketStateError(f"Winner seed {winner_seed} does not exist for event {event_id}.")
        winner_event_team_id = int(row["event_team_id"])

        await self.record_result(
            event_match_id=int(m["event_match_id"]),
            winner_event_team_id=winner_event_team_id,
            reported_by_account_id=reported_by_account_id,
            metadata=metadata,
        )

        return int(m["event_match_id"])

    async def advance(self, *, event_id: int) -> None:
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
                t1 = int(m["team1_event_team_id"])
                t2 = int(m["team2_event_team_id"]) if m.get("team2_event_team_id") is not None else None
                winners.append(t1 if t2 is None else t1)
            else:
                winners.append(int(w))
        return winners

    def _losers_in_order(self, ms: list[Mapping[str, Any]]) -> list[int]:
        losers: list[int] = []
        for m in ms:
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
            code = f"{bracket}{round_no}-{match_no:02d}" if bracket in ("W", "L") else f"GF-{match_no:02d}"
            md = dict(metadata) if metadata else {}
            md.setdefault("code", code)

            match_id = await self._repo.create_match(
                event_id=event_id,
                bracket=bracket,
                round_no=round_no,
                match_no=match_no,
                team1_event_team_id=t1,
                team2_event_team_id=t2,
                metadata=md,
            )
            if t2 is None:
                await self._set_bye_winner(event_match_id=match_id, winner_event_team_id=t1)
            return match_id
        except aiomysql.IntegrityError:
            return None

    async def _advance_single_elim(self, event_id: int) -> None:
        matches = await self._repo.list_matches(event_id=event_id)

        wb_rounds = sorted({int(m["round_no"]) for m in matches if str(m["bracket"]) == "W"})
        if not wb_rounds:
            return

        r = 1
        while True:
            curr = self._group(matches, "W", r)
            if not curr or not self._all_completed(curr):
                break

            winners = self._winners_in_order(curr)
            if len(winners) <= 1:
                break

            next_round = r + 1
            if self._group(matches, "W", next_round):
                r = next_round
                continue

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
        matches = await self._repo.list_matches(event_id=event_id)

        await self._advance_single_elim(event_id=event_id)
        matches = await self._repo.list_matches(event_id=event_id)

        wb_r1 = self._group(matches, "W", 1)
        if not wb_r1:
            return

        bracket_size = 2 * len(wb_r1)
        if bracket_size <= 2:
            # Two-team "double elim" behaves like single elim here (WB decides)
            return

        n = int(math.log2(bracket_size)) if bracket_size > 0 else 0

        def has_round(br: str, rn: int) -> bool:
            return bool(self._group(matches, br, rn))

        # LB round 1 from WB1 losers
        if self._all_completed(wb_r1) and not has_round("L", 1):
            losers = self._losers_in_order(wb_r1)
            await self._create_round_from_pairs(event_id, "L", 1, losers, metadata={"generated": True, "source": "WB1"})
            matches = await self._repo.list_matches(event_id=event_id)

        # For WB rounds 2..n-1 build alternating LB rounds (even cross, odd pure)
        for wb_round in range(2, max(2, n)):
            wb = self._group(matches, "W", wb_round)
            if not wb or not self._all_completed(wb):
                break

            lb_cross = 2 * wb_round - 2
            lb_prev = lb_cross - 1

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

            lb_pure = lb_cross + 1
            lb_cross_matches = self._group(matches, "L", lb_cross)
            if self._all_completed(lb_cross_matches) and not has_round("L", lb_pure):
                lb_winners2 = self._winners_in_order(lb_cross_matches)
                await self._create_round_from_pairs(event_id, "L", lb_pure, lb_winners2, metadata={"generated": True, "source": f"L{lb_cross}"})
                matches = await self._repo.list_matches(event_id=event_id)

        # WB final -> LB final -> GF
        wb_final = self._group(matches, "W", n)
        if not wb_final or not self._all_completed(wb_final):
            return

        wb_champ = self._winners_in_order(wb_final)[0]
        wb_final_losers = self._losers_in_order(wb_final)
        wb_final_loser = wb_final_losers[0] if wb_final_losers else None

        lb_last_pure = 2 * n - 3
        lb_last_cross = 2 * n - 2

        if not has_round("L", lb_last_pure):
            return
        lb_last_pure_matches = self._group(matches, "L", lb_last_pure)
        if not self._all_completed(lb_last_pure_matches):
            return
        lb_last_pure_winner = self._winners_in_order(lb_last_pure_matches)[0]

        if wb_final_loser is None:
            wb_final_loser = lb_last_pure_winner

        if not has_round("L", lb_last_cross):
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
        out: list[tuple[int, Optional[int]]] = []
        m = max(len(left), len(right))
        for i in range(m):
            t1 = left[i] if i < len(left) else None
            t2 = right[i] if i < len(right) else None
            if t1 is None and t2 is None:
                continue
            if t1 is None:
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
