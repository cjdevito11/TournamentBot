# domain/enums.py
from __future__ import annotations

from enum import Enum


class BracketKey(str, Enum):
    W = "W"     # Winners
    L = "L"     # Losers
    GF = "GF"   # Grand Finals


class EventFormat(str, Enum):
    SINGLE = "single_elim"
    DOUBLE = "double_elim"


class MatchStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    COMPLETED = "completed"
