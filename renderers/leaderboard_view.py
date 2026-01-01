# renderers/leaderboard_view.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _pad(s: str, width: int) -> str:
    s = s or ""
    if len(s) > width:
        return s[: max(0, width - 1)] + "…" if width >= 2 else s[:width]
    return s + (" " * (width - len(s)))


def _ratio(k: int, d: int) -> str:
    if d <= 0:
        return f"{k:.0f}.00"
    return f"{(k / d):.2f}"


@dataclass(frozen=True)
class LeaderboardOptions:
    max_rows: int = 15
    name_width: int = 18
    show_kda: bool = True
    show_participation: bool = True
    title: str = "Leaderboard"


class LeaderboardView:
    """
    Renders clean monospace tables for Discord.

    Expected input shapes (from StatsRepo):
      - player_totals rows: account_id, display_name, kills, deaths, assists, wins, losses, match_participations
      - team_records rows: event_team_id, team_name, wins, losses
    """

    def render_players(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        opts: LeaderboardOptions | None = None,
    ) -> str:
        o = opts or LeaderboardOptions()

        # slice + normalize
        data = list(rows)[: o.max_rows]

        headers = ["#", "Player", "W", "L", "K", "D", "A"]
        if o.show_kda:
            headers.append("K/D")
        if o.show_participation:
            headers.append("GP")

        # compute column widths
        idx_w = 3
        name_w = max(o.name_width, min(28, max((len(str(r.get("display_name") or "")) for r in data), default=o.name_width)))
        num_w = 4
        kd_w = 5

        lines: list[str] = []
        lines.append(f"=== {o.title} (Players) ===")
        lines.append(
            f"{_pad(headers[0], idx_w)} "
            f"{_pad(headers[1], name_w)} "
            f"{_pad('W', num_w)} {_pad('L', num_w)} "
            f"{_pad('K', num_w)} {_pad('D', num_w)} {_pad('A', num_w)}"
            + (f" {_pad('K/D', kd_w)}" if o.show_kda else "")
            + (f" {_pad('GP', num_w)}" if o.show_participation else "")
        )
        lines.append("-" * (idx_w + 1 + name_w + 1 + (num_w + 1) * 5 + (kd_w + 1 if o.show_kda else 0) + (num_w + 1 if o.show_participation else 0)))

        for i, r in enumerate(data, start=1):
            name = str(r.get("display_name") or r.get("username") or f"acct:{r.get('account_id')}")
            w = _safe_int(r.get("wins"))
            l = _safe_int(r.get("losses"))
            k = _safe_int(r.get("kills"))
            d = _safe_int(r.get("deaths"))
            a = _safe_int(r.get("assists"))
            gp = _safe_int(r.get("match_participations"))

            line = (
                f"{_pad(str(i), idx_w)} "
                f"{_pad(name, name_w)} "
                f"{_pad(str(w), num_w)} {_pad(str(l), num_w)} "
                f"{_pad(str(k), num_w)} {_pad(str(d), num_w)} {_pad(str(a), num_w)}"
            )
            if o.show_kda:
                line += f" {_pad(_ratio(k, d), kd_w)}"
            if o.show_participation:
                line += f" {_pad(str(gp), num_w)}"
            lines.append(line)

        return "```text\n" + "\n".join(lines).rstrip() + "\n```"

    def render_teams(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        title: str = "Leaderboard",
        max_rows: int = 16,
        name_width: int = 22,
        show_seed: bool = True,
    ) -> str:
        data = list(rows)[:max_rows]

        idx_w = 3
        seed_w = 5  # e.g. "[12]"
        name_w = max(name_width, min(30, max((len(str(r.get("team_name") or "")) for r in data), default=name_width)))
        num_w = 4

        lines: list[str] = []
        lines.append(f"=== {title} (Teams) ===")

        if show_seed:
            lines.append(
                f"{_pad('#', idx_w)} {_pad('Seed', seed_w)} {_pad('Team', name_w)} {_pad('W', num_w)} {_pad('L', num_w)}"
            )
            lines.append("-" * (idx_w + 1 + seed_w + 1 + name_w + 1 + num_w + 1 + num_w))
        else:
            lines.append(f"{_pad('#', idx_w)} {_pad('Team', name_w)} {_pad('W', num_w)} {_pad('L', num_w)}")
            lines.append("-" * (idx_w + 1 + name_w + 1 + num_w + 1 + num_w))

        for i, r in enumerate(data, start=1):
            name = str(r.get("team_name") or f"team:{r.get('event_team_id')}")
            w = _safe_int(r.get("wins"))
            l = _safe_int(r.get("losses"))

            if show_seed:
                seed = r.get("seed")
                seed_txt = f"[{_safe_int(seed)}]" if seed is not None else "[?]"
                lines.append(
                    f"{_pad(str(i), idx_w)} {_pad(seed_txt, seed_w)} {_pad(name, name_w)} {_pad(str(w), num_w)} {_pad(str(l), num_w)}"
                )
            else:
                lines.append(f"{_pad(str(i), idx_w)} {_pad(name, name_w)} {_pad(str(w), num_w)} {_pad(str(l), num_w)}")

        return "```text\n" + "\n".join(lines).rstrip() + "\n```"
