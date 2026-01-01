# domain/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from domain.enums import BracketKey


def match_code(bracket: str, round_no: int, match_no: int) -> str:
    b = bracket.upper()
    if b == "GF":
        return f"GF-{match_no:02d}"
    return f"{b}{round_no}-{match_no:02d}"


def next_power_of_two(n: int) -> int:
    if n <= 1:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def seeded_positions(n: int) -> list[int]:
    """
    Standard tournament seeding positions list (length n, n is power of two).
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


@dataclass(frozen=True)
class TeamRef:
    seed: int
    name: str
    event_team_id: Optional[int] = None  # present when known


@dataclass
class BracketNode:
    bracket: BracketKey
    round_no: int
    match_no: int

    # “planned” seeds for Round 1 (so we can draw full bracket even before matches exist)
    seed1: Optional[int] = None
    seed2: Optional[int] = None

    # actual event_team_ids once known (from DB matches)
    team1_event_team_id: Optional[int] = None
    team2_event_team_id: Optional[int] = None

    status: str = "pending"
    winner_event_team_id: Optional[int] = None

    @property
    def code(self) -> str:
        return match_code(self.bracket.value, self.round_no, self.match_no)
