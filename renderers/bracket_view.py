# renderers/bracket_view.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


def _json_obj(v: Any) -> dict:
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else (s[: max(0, n - 1)] + "…")


@dataclass(frozen=True)
class TeamInfo:
    seed: int | None
    name: str


class BracketView:
    """
    Renders a clean monospace bracket snapshot.

    Output goals:
      - Always show match_code (W1-01, L2-03, GF-01)
      - Always show seed numbers next to teams
      - Always show a reminder for the reporting syntax
    """

    def __init__(self, *, name_width: int = 22) -> None:
        self._name_width = max(10, int(name_width))

    def render(self, *, matches: Sequence[Mapping[str, Any]], teams: Sequence[Mapping[str, Any]], title: str, event_id: int | None = None) -> str:
        team_by_id: dict[int, TeamInfo] = {}
        for t in teams:
            tid = int(t["event_team_id"])
            seed = int(t["seed"]) if t.get("seed") is not None else None
            nm = str(t.get("display_name") or f"Team {seed or tid}")
            team_by_id[tid] = TeamInfo(seed=seed, name=nm)

        # group matches by bracket and round
        def sort_key(m: Mapping[str, Any]) -> tuple[int, int, int]:
            br = str(m.get("bracket") or "")
            br_rank = 2
            if br == "W":
                br_rank = 0
            elif br == "L":
                br_rank = 1
            return (br_rank, int(m.get("round_no") or 0), int(m.get("match_no") or 0))

        ms = sorted(list(matches), key=sort_key)

        winners: dict[int, list[Mapping[str, Any]]] = {}
        losers: dict[int, list[Mapping[str, Any]]] = {}
        gf: dict[int, list[Mapping[str, Any]]] = {}

        for m in ms:
            br = str(m.get("bracket") or "")
            rn = int(m.get("round_no") or 0)
            if br == "W":
                winners.setdefault(rn, []).append(m)
            elif br == "L":
                losers.setdefault(rn, []).append(m)
            elif br == "GF":
                gf.setdefault(rn, []).append(m)

        lines: list[str] = []
        lines.append(f"=== {title} ===")

        # consistent instructions right at the top
        if event_id is not None:
            lines.append(f"Report winners with: /event report event_id:{event_id} match_code:<W1-01> winner_seed:<1>")
        else:
            lines.append("Report winners with: /event report event_id:<id> match_code:<W1-01> winner_seed:<1>")

        def fmt_team(tid: int | None) -> str:
            if tid is None:
                return "[BYE]".ljust(6) + " " + "BYE".ljust(self._name_width)
            info = team_by_id.get(int(tid))
            if not info:
                return "[?]".ljust(6) + " " + _truncate(f"Team {tid}", self._name_width).ljust(self._name_width)
            seed_txt = f"[{info.seed}]" if info.seed is not None else "[?]"
            return seed_txt.ljust(6) + " " + _truncate(info.name, self._name_width).ljust(self._name_width)

        def winner_seed(m: Mapping[str, Any]) -> int | None:
            w = m.get("winner_event_team_id")
            if w is None:
                return None
            info = team_by_id.get(int(w))
            return info.seed if info else None

        def match_code(m: Mapping[str, Any]) -> str:
            md = _json_obj(m.get("metadata"))
            c = md.get("code")
            if isinstance(c, str) and c.strip():
                return c.strip().upper()
            br = str(m.get("bracket") or "")
            rn = int(m.get("round_no") or 0)
            mn = int(m.get("match_no") or 0)
            if br == "GF":
                return f"GF-{mn:02d}"
            return f"{br}{rn}-{mn:02d}"

        def status_badge(m: Mapping[str, Any]) -> str:
            st = str(m.get("status") or "").lower()
            if st == "completed":
                ws = winner_seed(m)
                return f"✅ W:{ws}" if ws is not None else "✅"
            return "⏳"

        def render_rounds(label: str, rounds: dict[int, list[Mapping[str, Any]]]) -> None:
            if not rounds:
                return
            lines.append("")
            lines.append(f"-- {label} --")
            for rn in sorted(rounds.keys()):
                lines.append(f"Round {rn}:")
                for m in sorted(rounds[rn], key=lambda x: int(x.get("match_no") or 0)):
                    code = match_code(m)
                    t1 = fmt_team(int(m["team1_event_team_id"]))
                    t2 = fmt_team(int(m["team2_event_team_id"])) if m.get("team2_event_team_id") is not None else fmt_team(None)
                    lines.append(f"  {code:<6}  {t1} vs {t2}  {status_badge(m)}")

        render_rounds("WINNERS", winners)
        render_rounds("LOSERS", losers)

        if gf:
            lines.append("")
            lines.append("-- GRAND FINALS --")
            for rn in sorted(gf.keys()):
                for m in sorted(gf[rn], key=lambda x: int(x.get("match_no") or 0)):
                    code = match_code(m)
                    t1 = fmt_team(int(m["team1_event_team_id"]))
                    t2 = fmt_team(int(m["team2_event_team_id"])) if m.get("team2_event_team_id") is not None else fmt_team(None)
                    lines.append(f"  {code:<6}  {t1} vs {t2}  {status_badge(m)}")

        return "```text\n" + "\n".join(lines) + "\n```"
