# renderers/bracket_view.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class BracketLine:
    left: str
    right: str


def _pad(s: str, width: int) -> str:
    s = s or ""
    if len(s) >= width:
        return s[: max(0, width - 1)] + "…" if width >= 2 else s[:width]
    return s + (" " * (width - len(s)))


def _team_label(team: Mapping[str, Any] | None, *, name_width: int = 18) -> str:
    if not team:
        return _pad("BYE", name_width)

    name = team.get("display_name") or team.get("team_name") or f"Team {team.get('event_team_id')}"
    seed = team.get("seed")
    if seed is not None:
        name = f"[{seed}] {name}"
    return _pad(str(name), name_width)


def _status_mark(m: Mapping[str, Any]) -> str:
    st = str(m.get("status") or "").lower()
    if st == "completed":
        w = m.get("winner_event_team_id")
        return f"✅ W:{w}" if w is not None else "✅"
    if st in ("open", "pending"):
        return "⏳"
    return "•"


class BracketView:
    """
    Text bracket renderer for Discord (monospace).

    Input expects:
      - matches: list of dict rows from event_match
      - teams: list of dict rows from event_team (optionally with display_name, seed)
    """

    def __init__(self, *, name_width: int = 20) -> None:
        self._name_width = int(name_width)

    def render(
        self,
        *,
        matches: list[Mapping[str, Any]],
        teams: list[Mapping[str, Any]],
        title: str = "Bracket",
        include_losers: bool = True,
        include_grand_finals: bool = True,
        max_lines: int = 55,
    ) -> str:
        team_map = {int(t["event_team_id"]): t for t in teams}

        def team(tid: Optional[int]) -> Optional[Mapping[str, Any]]:
            if tid is None:
                return None
            return team_map.get(int(tid))

        # Group matches
        wb = [m for m in matches if str(m.get("bracket")) == "W"]
        lb = [m for m in matches if str(m.get("bracket")) == "L"]
        gf = [m for m in matches if str(m.get("bracket")) == "GF"]

        wb.sort(key=lambda m: (int(m["round_no"]), int(m["match_no"])))
        lb.sort(key=lambda m: (int(m["round_no"]), int(m["match_no"])))
        gf.sort(key=lambda m: (int(m["round_no"]), int(m["match_no"])))

        lines: list[str] = []
        lines.append(f"=== {title} ===")
        lines.append("")

        # Winners Bracket
        lines.append("-- WINNERS --")
        lines.extend(self._render_section(wb, team, section_prefix="W"))
        lines.append("")

        # Losers Bracket
        if include_losers and lb:
            lines.append("-- LOSERS --")
            lines.extend(self._render_section(lb, team, section_prefix="L"))
            lines.append("")

        # Grand Finals
        if include_grand_finals and gf:
            lines.append("-- GRAND FINALS --")
            lines.extend(self._render_section(gf, team, section_prefix="GF"))
            lines.append("")

        # Trim if too long for Discord messages (keep end because finals matter)
        if len(lines) > max_lines:
            head = lines[:10]
            tail = lines[-(max_lines - 12) :]
            lines = head + ["...", ""] + tail

        return "```text\n" + "\n".join(lines).rstrip() + "\n```"

    def _render_section(
        self,
        matches: list[Mapping[str, Any]],
        team_lookup,
        *,
        section_prefix: str,
    ) -> list[str]:
        out: list[str] = []
        if not matches:
            out.append("(none)")
            return out

        curr_round: Optional[int] = None
        for m in matches:
            r = int(m["round_no"])
            if curr_round != r:
                curr_round = r
                out.append(f"Round {r}:")
            t1 = team_lookup(m.get("team1_event_team_id"))
            t2 = team_lookup(m.get("team2_event_team_id"))
            left = _team_label(t1, name_width=self._name_width)
            right = _team_label(t2, name_width=self._name_width) if t2 else _pad("BYE", self._name_width)
            mark = _status_mark(m)
            out.append(f"  {section_prefix}{r}-{int(m['match_no']):02d}  {left} vs {right}  {mark}")
        return out
