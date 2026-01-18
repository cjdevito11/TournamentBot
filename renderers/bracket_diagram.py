# renderers/bracket_diagram.py
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import log2
from typing import Any, Mapping, Optional

import os
from PIL import Image, ImageDraw, ImageFont

from domain.enums import BracketKey, EventFormat
from domain.models import BracketNode, match_code, next_power_of_two, seeded_positions


@dataclass(frozen=True)
class DiagramStyle:
    # Layout (logical units; final pixels = logical * scale)
    margin: int = 36
    box_w: int = 420
    box_h: int = 180  # FULL match block height (two team panels stacked)
    h_gap: int = 120
    v_gap: int = 22

    # Render scale (keeps layout math stable; improves readability)
    scale: float = 1.5  # 1.0, 1.5, 2.0

    # Background (solid fallback if no bg image)
    bg: tuple[int, int, int] = (10, 10, 12)

    # Theme colors (Diablo-ish: iron, ember, gore)
    text: tuple[int, int, int] = (240, 232, 220)
    subtle: tuple[int, int, int] = (170, 160, 150)

    # Box theme
    box_fill_top: tuple[int, int, int] = (28, 24, 24)      # dark iron
    box_fill_bottom: tuple[int, int, int] = (16, 14, 16)   # deeper shadow
    box_border: tuple[int, int, int] = (128, 118, 110)     # worn steel
    box_inner_border: tuple[int, int, int] = (55, 50, 48)  # inner bevel shadow

    # Accents
    ember: tuple[int, int, int] = (214, 112, 38)           # orange ember
    blood: tuple[int, int, int] = (150, 25, 25)            # gore red
    winner_gold: tuple[int, int, int] = (210, 175, 90)     # “unique item” gold
    line: tuple[int, int, int] = (90, 78, 72)              # bracket connector

    # Typography
    font_size: int = 28
    font_size_small: int = 22

    # Assets (optional)
    bg_image_path: str | None = "assets/bracket_bg.png"
    team_box_image_path: str | None = None
    box_image_path: str | None = None
    winner_box_image_path: str | None = None


class BracketDiagramRenderer:
    def __init__(self, style: DiagramStyle | None = None, font_path: Optional[str] = None) -> None:
        self.style = style or DiagramStyle()
        self.font_path = font_path

    def _safe_load_rgba(self, path: str | None) -> Image.Image | None:
        if not path:
            return None
        try:
            if not os.path.exists(path):
                return None
            return Image.open(path).convert("RGBA")
        except Exception:
            return None

    def _fit_cover(self, img: Image.Image, w: int, h: int) -> Image.Image:
        iw, ih = img.size
        if iw <= 0 or ih <= 0:
            return img.resize((w, h))
        scale = max(w / iw, h / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        resized = img.resize((nw, nh), Image.LANCZOS)
        left = (nw - w) // 2
        top = (nh - h) // 2
        return resized.crop((left, top, left + w, top + h))

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
        # -----------------------------
        # Resolve format + build nodes
        # -----------------------------
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

        # Seed placement for W1
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

        # Apply match rows from DB
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

        # -----------------------------
        # Layout (logical units)
        # -----------------------------
        style = self.style

        s = float(getattr(style, "scale", 1.0) or 1.0)

        def S(v: int) -> int:
            return int(v * s)

        box_w_l, box_h_l = style.box_w, style.box_h
        h_gap_l, v_gap_l = style.h_gap, style.v_gap
        margin_l = style.margin

        winners_xy: dict[str, tuple[int, int]] = {}
        step = (box_h_l + v_gap_l) * 2

        w1_count = bracket_size // 2
        for m in range(1, w1_count + 1):
            code = match_code("W", 1, m)
            x = margin_l + 0 * (box_w_l + h_gap_l)
            y = margin_l + (m - 1) * step
            winners_xy[code] = (x, y)

        for r in range(2, k + 1):
            match_count = bracket_size // (2**r)
            x = margin_l + (r - 1) * (box_w_l + h_gap_l)
            for m in range(1, match_count + 1):
                src1 = match_code("W", r - 1, 2 * m - 1)
                src2 = match_code("W", r - 1, 2 * m)
                y1 = winners_xy.get(src1, (0, margin_l))[1]
                y2 = winners_xy.get(src2, (0, margin_l))[1]
                y = (y1 + y2) // 2
                winners_xy[match_code("W", r, m)] = (x, y)

        max_wy = max(y for (_, y) in winners_xy.values()) if winners_xy else margin_l
        winners_height = max_wy + box_h_l + margin_l

        losers_xy: dict[str, tuple[int, int]] = {}
        losers_offset_y = winners_height + 80
        gf_xy: tuple[int, int] | None = None

        if is_double and losers_rounds > 0:
            for m in range(1, (bracket_size // 4) + 1):
                w_src1 = match_code("W", 1, 2 * m - 1)
                w_src2 = match_code("W", 1, 2 * m)
                y1 = winners_xy.get(w_src1, (0, margin_l))[1]
                y2 = winners_xy.get(w_src2, (0, margin_l))[1]
                y = losers_offset_y + ((y1 + y2) // 2)
                x = margin_l + 0 * (box_w_l + h_gap_l)
                losers_xy[match_code("L", 1, m)] = (x, y)

            for r in range(2, losers_rounds + 1):
                stage = (r + 1) // 2
                match_count = bracket_size // (2 ** (stage + 1))
                x = margin_l + (r - 1) * (box_w_l + h_gap_l)

                for m in range(1, match_count + 1):
                    code = match_code("L", r, m)

                    if r % 2 == 0:
                        prev = match_code("L", r - 1, m)
                        py = losers_xy.get(prev, (0, losers_offset_y + margin_l))[1]
                        losers_xy[code] = (x, py)
                    else:
                        src1 = match_code("L", r - 1, 2 * m - 1)
                        src2 = match_code("L", r - 1, 2 * m)
                        y1 = losers_xy.get(src1, (0, losers_offset_y + margin_l))[1]
                        y2 = losers_xy.get(src2, (0, losers_offset_y + margin_l))[1]
                        y = (y1 + y2) // 2
                        losers_xy[code] = (x, y)

            gf_x = margin_l + (max(k, losers_rounds)) * (box_w_l + h_gap_l) + 40
            w_final_y = winners_xy.get(match_code("W", k, 1), (0, margin_l))[1]
            l_final_y = losers_xy.get(match_code("L", losers_rounds, 1), (0, losers_offset_y + margin_l))[1]
            gf_y = (w_final_y + l_final_y) // 2
            gf_xy = (gf_x, gf_y)

        all_points = list(winners_xy.values())
        if is_double and gf_xy is not None:
            all_points += list(losers_xy.values())
            all_points.append(gf_xy)

        max_x = max(x for (x, _) in all_points) if all_points else margin_l
        max_y = max(y for (_, y) in all_points) if all_points else margin_l

        width_l = max_x + box_w_l + margin_l
        height_l = max_y + box_h_l + margin_l

        width = max(2, S(width_l))
        height = max(2, S(height_l))

        # -----------------------------
        # Image + background
        # -----------------------------
        img = Image.new("RGBA", (width, height), (*style.bg, 255))
        bg = self._safe_load_rgba(style.bg_image_path)
        if bg:
            bg_fit = self._fit_cover(bg, width, height)
            img.alpha_composite(bg_fit, (0, 0))

        draw = ImageDraw.Draw(img)

        # Scaled fonts
        f_main = self._font(max(10, int(style.font_size * s)))
        f_small = self._font(max(10, int(style.font_size_small * s)))

        # -----------------------------
        # Text helpers
        # -----------------------------
        def split_players(team_display: str) -> tuple[str, str]:
            s0 = (team_display or "").strip()
            parts = [p.strip() for p in s0.split(" + ") if p.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]
            if len(parts) == 1:
                return parts[0], ""
            return "TBD", ""

        def ellipsize(text: str, font: ImageFont.ImageFont, max_w: int) -> str:
            t = (text or "").strip()
            if not t:
                return ""
            if draw.textlength(t, font=font) <= max_w:
                return t
            while t and draw.textlength(t + "…", font=font) > max_w:
                t = t[:-1]
            return (t + "…") if t else "…"

        def team_line(seed: Optional[int], event_team_id: Optional[int]) -> str:
            if event_team_id is not None and int(event_team_id) in event_team_id_to_seed:
                s_seed = event_team_id_to_seed[int(event_team_id)]
                t = teams_by_seed.get(int(s_seed))
                nm = (t or {}).get("display_name") or (t or {}).get("name") or f"Seed {s_seed}"
                return f"[{s_seed}] {str(nm)}"
            if seed is not None:
                t = teams_by_seed.get(int(seed))
                if t:
                    nm = str(t.get("display_name") or t.get("name") or f"Seed {seed}")
                    return f"[{seed}] {nm}"
                if seed > team_count:
                    return "BYE"
                return f"[{seed}] TBD"
            return "TBD"

        # -----------------------------
        # Diablo-ish fallback box renderer
        # -----------------------------
        box_w = S(box_w_l)
        box_h = S(box_h_l)

        inner_gap = S(10)
        header_h = S(28)
        team_h = max(S(40), (box_h - header_h - inner_gap) // 2)

        def _draw_gore_splatter(x: int, y: int, w: int, h: int, intensity: float) -> None:
            """
            Tiny deterministic splatter to break up flat UI.
            Kept subtle so it doesn't ruin readability.
            """
            if intensity <= 0:
                return
            # determinism per box
            seed = (x * 1315423911) ^ (y * 2654435761) ^ (w * 97531) ^ (h * 19207)
            # simple LCG
            def rnd() -> int:
                nonlocal seed
                seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
                return seed

            n = int(6 * intensity) + 4
            for _ in range(n):
                px = x + (rnd() % w)
                py = y + (rnd() % h)
                r = 1 + (rnd() % max(2, S(4)))
                a = 80 + (rnd() % 90)
                c = (*style.blood, a)
                draw.ellipse([px - r, py - r, px + r, py + r], fill=c)

        def _draw_team_panel(px: int, py: int, team_text: str, *, is_winner: bool) -> None:
            """
            Draw one team panel (fallback, no image templates).
            Two name lines (player1, player2). Adds a left “gore orb” accent.
            """
            pw = box_w
            ph = team_h

            # Panel gradient (top->bottom)
            top = style.box_fill_top
            bot = style.box_fill_bottom
            # Draw gradient as a few bands (cheap but looks good)
            bands = 10
            for i in range(bands):
                t = i / max(1, bands - 1)
                col = (
                    int(top[0] * (1 - t) + bot[0] * t),
                    int(top[1] * (1 - t) + bot[1] * t),
                    int(top[2] * (1 - t) + bot[2] * t),
                    235,
                )
                y0 = py + int((ph * i) / bands)
                y1 = py + int((ph * (i + 1)) / bands)
                draw.rectangle([px, y0, px + pw, y1], fill=col)

            # Outer border + inner bevel (gothic frame feel)
            draw.rounded_rectangle(
                [px, py, px + pw, py + ph],
                radius=S(10),
                outline=(*style.box_border, 255),
                width=max(2, S(2)),
            )
            draw.rounded_rectangle(
                [px + S(2), py + S(2), px + pw - S(2), py + ph - S(2)],
                radius=S(9),
                outline=(*style.box_inner_border, 255),
                width=max(1, S(1)),
            )

            # Winner glow strip
            if is_winner:
                glow_h = max(S(6), int(ph * 0.10))
                draw.rectangle([px + S(2), py + S(2), px + pw - S(2), py + S(2) + glow_h], fill=(*style.winner_gold, 85))

            # Left “orb” accent:
            # - default: blood ruby
            # - winner: green emerald
            orb_cx = px + S(26)
            orb_cy = py + ph // 2
            orb_r = max(S(10), int(ph * 0.18))

            if is_winner:
                orb_base = (34, 150, 70, 220)   # emerald green
                orb_core = (120, 235, 170, 170) # bright core
                orb_ring = (170, 220, 190, 220) # pale ring
            else:
                orb_base = (*style.blood, 220)
                orb_core = (*style.ember, 160)
                orb_ring = (*style.box_border, 220)

            draw.ellipse([orb_cx - orb_r, orb_cy - orb_r, orb_cx + orb_r, orb_cy + orb_r], fill=orb_base)
            draw.ellipse(
                [orb_cx - int(orb_r * 0.55), orb_cy - int(orb_r * 0.55), orb_cx + int(orb_r * 0.55), orb_cy + int(orb_r * 0.55)],
                fill=orb_core,
            )
            draw.ellipse([orb_cx - orb_r, orb_cy - orb_r, orb_cx + orb_r, orb_cy + orb_r], outline=orb_ring, width=max(1, S(1)))

            # Inner name “slots” (inset bars)
            slot_x0 = px + S(56)
            slot_x1 = px + pw - S(18)
            slot_h = max(S(22), int(ph * 0.28))
            slot_gap = max(S(8), int(ph * 0.08))

            slot1_y0 = py + max(S(10), int((ph - (2 * slot_h + slot_gap)) / 2))
            slot2_y0 = slot1_y0 + slot_h + slot_gap

            # Slot fills (dark + subtle red tint)
            draw.rounded_rectangle([slot_x0, slot1_y0, slot_x1, slot1_y0 + slot_h], radius=S(8), fill=(10, 10, 12, 190))
            draw.rounded_rectangle([slot_x0, slot2_y0, slot_x1, slot2_y0 + slot_h], radius=S(8), fill=(10, 10, 12, 190))
            draw.rounded_rectangle([slot_x0, slot1_y0, slot_x1, slot1_y0 + slot_h], radius=S(8), outline=(*style.box_inner_border, 200), width=max(1, S(1)))
            draw.rounded_rectangle([slot_x0, slot2_y0, slot_x1, slot2_y0 + slot_h], radius=S(8), outline=(*style.box_inner_border, 200), width=max(1, S(1)))

            # Light splatter (very subtle) to fit your war/gore background
            _draw_gore_splatter(px + S(6), py + S(6), pw - S(12), ph - S(12), intensity=0.8 if is_winner else 0.45)

            # Player lines
            p1, p2 = split_players(team_text)

            # Fit to slot width
            max_text_w = (slot_x1 - slot_x0) - S(14)
            p1 = ellipsize(p1, f_main, max_text_w)
            p2 = ellipsize(p2, f_main, max_text_w)

            text_x = slot_x0 + S(10)
            # Center vertically within slots
            t1_y = slot1_y0 + max(S(2), int((slot_h - (style.font_size * s)) / 4))
            t2_y = slot2_y0 + max(S(2), int((slot_h - (style.font_size * s)) / 4))

            fill1 = style.text if not is_winner else (255, 245, 225)
            fill2 = style.text

            draw.text((text_x, t1_y), p1, font=f_main, fill=fill1)
            draw.text((text_x, t2_y), p2, font=f_main, fill=fill2)


        def draw_box(x_l: int, y_l: int, node: BracketNode) -> None:
            # scaled coords
            x = S(x_l)
            y = S(y_l)

            status = (node.status or "pending").lower()
            hdr = f"{node.code}  {status.upper()}"

            # Header strip (gothic steel)
            hx0, hy0 = x, y
            hx1, hy1 = x + box_w, y + header_h
            draw.rectangle([hx0, hy0, hx1, hy1], fill=(8, 8, 10, 220))
            draw.rectangle([hx0, hy0, hx1, hy1], outline=(*style.box_border, 220), width=max(1, S(1)))

            # Ember notch
            notch_w = S(10)
            draw.rectangle([hx0, hy0, hx0 + notch_w, hy1], fill=(*style.ember, 140))

            draw.text((x + S(14), y + S(4)), hdr, font=f_small, fill=style.subtle)

            t1 = team_line(node.seed1, node.team1_event_team_id)
            t2 = team_line(node.seed2, node.team2_event_team_id)

            # Remove seed prefixes for player splitting
            if t1.startswith("[") and "]" in t1:
                t1 = t1.split("] ", 1)[-1]
            if t2.startswith("[") and "]" in t2:
                t2 = t2.split("] ", 1)[-1]

            top_is_winner = False
            bot_is_winner = False
            if node.winner_event_team_id is not None:
                if node.team1_event_team_id == node.winner_event_team_id:
                    top_is_winner = True
                elif node.team2_event_team_id == node.winner_event_team_id:
                    bot_is_winner = True

            top_y = y + header_h
            bot_y = top_y + team_h + inner_gap

            _draw_team_panel(x, top_y, t1, is_winner=top_is_winner)
            _draw_team_panel(x, bot_y, t2, is_winner=bot_is_winner)

            # VS marker (subtle)
            vs_y = top_y + team_h + (inner_gap // 2) - S(10)
            draw.text((x + box_w - S(46), vs_y), "VS", font=f_small, fill=style.subtle)

        def draw_edge(src_xy_l: tuple[int, int], dst_xy_l: tuple[int, int]) -> None:
            sx_l, sy_l = src_xy_l
            dx_l, dy_l = dst_xy_l

            sx, sy = S(sx_l), S(sy_l)
            dx, dy = S(dx_l), S(dy_l)

            s_mid = (sx + box_w, sy + box_h // 2)
            d_mid = (dx, dy + box_h // 2)

            mid_x = (s_mid[0] + d_mid[0]) // 2

            pts = [s_mid, (mid_x, s_mid[1]), (mid_x, d_mid[1]), d_mid]
            draw.line(pts, fill=style.line, width=max(2, S(2)))

        # -----------------------------
        # Title
        # -----------------------------
        if title:
            draw.text((S(margin_l), S(10)), title, font=f_main, fill=style.text)

        # -----------------------------
        # Edges
        # -----------------------------
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

        # -----------------------------
        # Boxes
        # -----------------------------
        for code, (x_l, y_l) in winners_xy.items():
            node = nodes.get(code)
            if node:
                draw_box(x_l, y_l, node)

        if is_double and gf_xy is not None:
            for code, (x_l, y_l) in losers_xy.items():
                node = nodes.get(code)
                if node:
                    draw_box(x_l, y_l, node)

            gf_node = nodes.get("GF-01")
            if gf_node:
                draw_box(gf_xy[0], gf_xy[1], gf_node)

        # -----------------------------
        # Export
        # -----------------------------
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
