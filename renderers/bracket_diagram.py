# renderers/bracket_diagram.py
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import log2
from typing import Any, Mapping, Optional

from PIL import Image, ImageDraw, ImageFont

from domain.enums import BracketKey, EventFormat
from domain.models import BracketNode, match_code, next_power_of_two, seeded_positions


@dataclass(frozen=True)
class DiagramStyle:
    margin: int = 36
    box_w: int = 380
    box_h: int = 78
    h_gap: int = 110
    v_gap: int = 18

    bg: tuple[int, int, int] = (14, 16, 22)
    box_bg: tuple[int, int, int] = (20, 23, 31)
    box_border: tuple[int, int, int] = (120, 130, 155)
    text: tuple[int, int, int] = (235, 238, 244)
    subtle: tuple[int, int, int] = (150, 158, 176)
    line: tuple[int, int, int] = (95, 103, 120)

    font_size: int = 16
    font_size_small: int = 14


class BracketDiagramRenderer:
    def __init__(self, style: DiagramStyle | None = None, font_path: Optional[str] = None) -> None:
        self.style = style or DiagramStyle()
        self.font_path = font_path

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            if self.font_path:
                return ImageFont.truetype(self.font_path, size)
            for name in ("DejaVuSansMono.ttf", "Consolas.ttf", "consola.ttf", "Courier New.ttf"):
                try:
                    return ImageFont.truetype(name, size)
                except Exception:
                    continue
        except Exception:
            pass
        return ImageFont.load_default()

    def render_png(
        self,
        *,
        event_id: int,
        event_format: str,
        teams_by_seed: Mapping[int, Mapping[str, Any]],
        matches: list[Mapping[str, Any]],
        title: str | None = None,
    ) -> bytes:
        fmt = str(event_format or "").lower()
        is_double = fmt == EventFormat.DOUBLE.value

        team_count = len(teams_by_seed)
        bracket_size = next_power_of_two(max(2, team_count))
        k = int(log2(bracket_size))

        nodes: dict[str, BracketNode] = {}

        for r in range(1, k + 1):
            match_count = bracket_size // (2**r)
            for m in range(1, match_count + 1):
                n = BracketNode(bracket=BracketKey.W, round_no=r, match_no=m)
                nodes[n.code] = n

        losers_rounds = 0
        if is_double:
            losers_rounds = 2 * (k - 1)
            for r in range(1, losers_rounds + 1):
                stage = (r + 1) // 2
                match_count = bracket_size // (2 ** (stage + 1))
                for m in range(1, match_count + 1):
                    n = BracketNode(bracket=BracketKey.L, round_no=r, match_no=m)
                    nodes[n.code] = n

            gf = BracketNode(bracket=BracketKey.GF, round_no=1, match_no=1)
            nodes[gf.code] = gf

        pos = seeded_positions(bracket_size)
        for i in range(0, bracket_size, 2):
            seed1 = pos[i]
            seed2 = pos[i + 1]
            m_no = (i // 2) + 1
            code = match_code("W", 1, m_no)
            n = nodes.get(code)
            if n:
                n.seed1 = seed1
                n.seed2 = seed2

        event_team_id_to_seed: dict[int, int] = {}
        for seed, t in teams_by_seed.items():
            etid = t.get("event_team_id")
            if etid is not None:
                event_team_id_to_seed[int(etid)] = int(seed)

        for row in matches:
            b = str(row.get("bracket") or "W").upper()
            r = int(row.get("round_no") or 1)
            m = int(row.get("match_no") or 1)
            code = match_code(b, r, m)
            n = nodes.get(code)
            if not n:
                continue

            n.status = str(row.get("status") or "pending")
            t1 = row.get("team1_event_team_id")
            t2 = row.get("team2_event_team_id")
            n.team1_event_team_id = int(t1) if t1 is not None else None
            n.team2_event_team_id = int(t2) if t2 is not None else None
            w = row.get("winner_event_team_id")
            n.winner_event_team_id = int(w) if w is not None else None

        style = self.style
        box_w, box_h = style.box_w, style.box_h
        h_gap, v_gap = style.h_gap, style.v_gap
        margin = style.margin

        winners_xy: dict[str, tuple[int, int]] = {}
        step = (box_h + v_gap) * 2

        w1_count = bracket_size // 2
        for m in range(1, w1_count + 1):
            code = match_code("W", 1, m)
            x = margin + 0 * (box_w + h_gap)
            y = margin + (m - 1) * step
            winners_xy[code] = (x, y)

        for r in range(2, k + 1):
            match_count = bracket_size // (2**r)
            x = margin + (r - 1) * (box_w + h_gap)
            for m in range(1, match_count + 1):
                src1 = match_code("W", r - 1, 2 * m - 1)
                src2 = match_code("W", r - 1, 2 * m)
                y1 = winners_xy.get(src1, (0, margin))[1]
                y2 = winners_xy.get(src2, (0, margin))[1]
                y = (y1 + y2) // 2
                winners_xy[match_code("W", r, m)] = (x, y)

        max_wy = max(y for (_, y) in winners_xy.values()) if winners_xy else margin
        winners_height = max_wy + box_h + margin

        losers_xy: dict[str, tuple[int, int]] = {}
        losers_offset_y = winners_height + 80
        gf_xy: tuple[int, int] | None = None

        if is_double and losers_rounds > 0:
            for m in range(1, (bracket_size // 4) + 1):
                w_src1 = match_code("W", 1, 2 * m - 1)
                w_src2 = match_code("W", 1, 2 * m)
                y1 = winners_xy.get(w_src1, (0, margin))[1]
                y2 = winners_xy.get(w_src2, (0, margin))[1]
                y = losers_offset_y + ((y1 + y2) // 2)
                x = margin + 0 * (box_w + h_gap)
                losers_xy[match_code("L", 1, m)] = (x, y)

            for r in range(2, losers_rounds + 1):
                stage = (r + 1) // 2
                match_count = bracket_size // (2 ** (stage + 1))
                x = margin + (r - 1) * (box_w + h_gap)

                for m in range(1, match_count + 1):
                    code = match_code("L", r, m)

                    if r % 2 == 0:
                        prev = match_code("L", r - 1, m)
                        py = losers_xy.get(prev, (0, losers_offset_y + margin))[1]
                        losers_xy[code] = (x, py)
                    else:
                        src1 = match_code("L", r - 1, 2 * m - 1)
                        src2 = match_code("L", r - 1, 2 * m)
                        y1 = losers_xy.get(src1, (0, losers_offset_y + margin))[1]
                        y2 = losers_xy.get(src2, (0, losers_offset_y + margin))[1]
                        y = (y1 + y2) // 2
                        losers_xy[code] = (x, y)

            gf_x = margin + (max(k, losers_rounds)) * (box_w + h_gap) + 40
            w_final_y = winners_xy.get(match_code("W", k, 1), (0, margin))[1]
            l_final_y = losers_xy.get(match_code("L", losers_rounds, 1), (0, losers_offset_y + margin))[1]
            gf_y = (w_final_y + l_final_y) // 2
            gf_xy = (gf_x, gf_y)

        all_points = list(winners_xy.values())
        if is_double and gf_xy is not None:
            all_points += list(losers_xy.values())
            all_points.append(gf_xy)

        max_x = max(x for (x, _) in all_points) if all_points else margin
        max_y = max(y for (_, y) in all_points) if all_points else margin

        width = max_x + box_w + margin
        height = max_y + box_h + margin

        img = Image.new("RGB", (width, height), style.bg)
        draw = ImageDraw.Draw(img)

        f_main = self._font(style.font_size)
        f_small = self._font(style.font_size_small)

        if title:
            draw.text((margin, 10), title, font=f_main, fill=style.text)

        def team_line(seed: Optional[int], event_team_id: Optional[int]) -> str:
            if event_team_id is not None and int(event_team_id) in event_team_id_to_seed:
                s = event_team_id_to_seed[int(event_team_id)]
                t = teams_by_seed.get(int(s))
                nm = (t or {}).get("display_name") or (t or {}).get("name") or f"Seed {s}"
                return f"[{s}] {str(nm)}"
            if seed is not None:
                t = teams_by_seed.get(int(seed))
                if t:
                    nm = str(t.get("display_name") or t.get("name") or f"Seed {seed}")
                    return f"[{seed}] {nm}"
                if seed > team_count:
                    return "BYE"
                return f"[{seed}] TBD"
            return "TBD"

        def draw_box(x: int, y: int, node: BracketNode) -> None:
            x2, y2 = x + box_w, y + box_h
            draw.rectangle([x, y, x2, y2], fill=style.box_bg, outline=style.box_border, width=2)

            status = (node.status or "pending").lower()
            hdr = f"{node.code}  {status.upper()}"
            draw.text((x + 10, y + 8), hdr, font=f_small, fill=style.subtle)

            t1 = team_line(node.seed1, node.team1_event_team_id)
            t2 = team_line(node.seed2, node.team2_event_team_id)

            if node.winner_event_team_id is not None:
                if node.team1_event_team_id == node.winner_event_team_id:
                    t1 = f"{t1}  Winner!"
                elif node.team2_event_team_id == node.winner_event_team_id:
                    t2 = f"{t2}  Winner!"

            draw.text((x + 10, y + 30), t1[:52], font=f_main, fill=style.text)
            draw.text((x + 10, y + 52), t2[:52], font=f_main, fill=style.text)

        def draw_edge(src_xy: tuple[int, int], dst_xy: tuple[int, int]) -> None:
            sx, sy = src_xy
            dx, dy = dst_xy
            s_mid = (sx + box_w, sy + box_h // 2)
            d_mid = (dx, dy + box_h // 2)

            mid_x = (s_mid[0] + d_mid[0]) // 2
            pts = [s_mid, (mid_x, s_mid[1]), (mid_x, d_mid[1]), d_mid]
            draw.line(pts, fill=style.line, width=2)

        for r in range(1, k):
            match_count = bracket_size // (2**r)
            for m in range(1, match_count + 1):
                src = match_code("W", r, m)
                dst = match_code("W", r + 1, (m + 1) // 2)
                if src in winners_xy and dst in winners_xy:
                    draw_edge(winners_xy[src], winners_xy[dst])

        if is_double and gf_xy is not None and losers_rounds > 0:
            for m in range(1, (bracket_size // 2) + 1):
                src = match_code("W", 1, m)
                dst = match_code("L", 1, (m + 1) // 2)
                if src in winners_xy and dst in losers_xy:
                    draw_edge(winners_xy[src], losers_xy[dst])

            for r in range(2, k + 1):
                for m in range(1, (bracket_size // (2**r)) + 1):
                    src = match_code("W", r, m)
                    dst = match_code("L", 2 * (r - 1), m)
                    if src in winners_xy and dst in losers_xy:
                        draw_edge(winners_xy[src], losers_xy[dst])

            for r in range(1, losers_rounds):
                stage = (r + 1) // 2
                match_count = bracket_size // (2 ** (stage + 1))
                for m in range(1, match_count + 1):
                    src = match_code("L", r, m)
                    if r % 2 == 1:
                        dst = match_code("L", r + 1, m)
                    else:
                        dst = match_code("L", r + 1, (m + 1) // 2)
                    if src in losers_xy and dst in losers_xy:
                        draw_edge(losers_xy[src], losers_xy[dst])

            w_final = match_code("W", k, 1)
            l_final = match_code("L", losers_rounds, 1)
            if w_final in winners_xy:
                draw_edge(winners_xy[w_final], gf_xy)
            if l_final in losers_xy:
                draw_edge(losers_xy[l_final], gf_xy)

        for code, (x, y) in winners_xy.items():
            node = nodes.get(code)
            if node:
                draw_box(x, y, node)

        if is_double and gf_xy is not None:
            for code, (x, y) in losers_xy.items():
                node = nodes.get(code)
                if node:
                    draw_box(x, y, node)

            gf_node = nodes.get("GF-01")
            if gf_node:
                draw_box(gf_xy[0], gf_xy[1], gf_node)

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
