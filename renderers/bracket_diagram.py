# renderers/bracket_diagram.py
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import ceil, log2
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
    winner_green: tuple[int, int, int] = (34, 150, 70)     # emerald (winner)

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

    # -----------------------------
    # Low-level utils
    # -----------------------------
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

    def _bg_canvas(self, *, width: int, height: int) -> Image.Image:
        """
        Shared background creation so both full bracket and current-round views look consistent.
        """
        style = self.style
        img = Image.new("RGBA", (max(2, int(width)), max(2, int(height))), (*style.bg, 255))
        bg = self._safe_load_rgba(style.bg_image_path)
        if bg:
            bg_fit = self._fit_cover(bg, img.size[0], img.size[1])
            img.alpha_composite(bg_fit, (0, 0))
        return img

    # -----------------------------
    # Shared "data prep" (no drawing)
    # -----------------------------
    def _build_nodes(
        self,
        *,
        event_format: str,
        teams_by_seed: Mapping[int, Mapping[str, Any]],
        matches: list[Mapping[str, Any]],
    ) -> tuple[dict[str, BracketNode], dict[int, int], int, int, int, bool, int]:
        """
        Returns:
          nodes: code -> BracketNode (with seeds + team ids + winner ids + status)
          event_team_id_to_seed: event_team_id -> seed
          team_count
          bracket_size (power of two)
          k (log2(bracket_size))
          is_double
          losers_rounds
        """
        fmt = str(event_format or "").lower()
        is_double = fmt == EventFormat.DOUBLE.value

        team_count = len(teams_by_seed)
        bracket_size = next_power_of_two(max(2, team_count))
        k = int(log2(bracket_size))

        nodes: dict[str, BracketNode] = {}

        # Winners bracket nodes
        for r in range(1, k + 1):
            match_count = bracket_size // (2**r)
            for m in range(1, match_count + 1):
                n = BracketNode(bracket=BracketKey.W, round_no=r, match_no=m)
                nodes[n.code] = n

        # Losers + GF nodes
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

        return nodes, event_team_id_to_seed, team_count, bracket_size, k, is_double, losers_rounds

    def _compute_wl(self, *, teams_by_seed: Mapping[int, Mapping[str, Any]], matches: list[Mapping[str, Any]]) -> dict[int, dict[str, int]]:
        """
        event_team_id -> {'w': int, 'l': int}
        """
        stats: dict[int, dict[str, int]] = {}
        for _seed, t in teams_by_seed.items():
            etid = t.get("event_team_id")
            if etid is not None:
                stats[int(etid)] = {"w": 0, "l": 0}

        for row in matches:
            if str(row.get("status") or "").lower() != "completed":
                continue
            w = row.get("winner_event_team_id")
            l = row.get("loser_event_team_id")
            if w is not None:
                wid = int(w)
                stats.setdefault(wid, {"w": 0, "l": 0})
                stats[wid]["w"] += 1
            if l is not None:
                lid = int(l)
                stats.setdefault(lid, {"w": 0, "l": 0})
                stats[lid]["l"] += 1
        return stats

    # -----------------------------
    # Shared text helpers for drawing
    # -----------------------------
    @staticmethod
    def _split_players(team_display: str) -> tuple[str, str]:
        s0 = (team_display or "").strip()
        parts = [p.strip() for p in s0.split(" + ") if p.strip()]
        if len(parts) >= 2:
            return parts[0], parts[1]
        if len(parts) == 1:
            return parts[0], ""
        return "TBD", ""

    def _team_label_and_seed(
        self,
        *,
        seed: Optional[int],
        event_team_id: Optional[int],
        event_team_id_to_seed: Mapping[int, int],
        teams_by_seed: Mapping[int, Mapping[str, Any]],
        team_count: int,
    ) -> tuple[str, Optional[int]]:
        # Prefer event_team_id -> seed mapping
        if event_team_id is not None:
            s_seed = event_team_id_to_seed.get(int(event_team_id))
            if s_seed is not None:
                t = teams_by_seed.get(int(s_seed))
                nm = (t or {}).get("display_name") or (t or {}).get("name") or f"Seed {s_seed}"
                return (str(nm), int(s_seed))

        # Fallback to seeded slot (initial placement / TBD / BYE)
        if seed is not None:
            t = teams_by_seed.get(int(seed))
            if t:
                nm = str(t.get("display_name") or t.get("name") or f"Seed {seed}")
                return (nm, int(seed))
            if seed > team_count:
                return ("BYE", int(seed))
            return ("TBD", int(seed))

        return ("TBD", None)

    # -----------------------------
    # Shared drawing: match card
    # -----------------------------
    def _draw_match_card(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        w: int,
        h: int,
        header: str,
        top_text: str,
        top_seed: Optional[int],
        top_is_winner: bool,
        bot_text: str,
        bot_seed: Optional[int],
        bot_is_winner: bool,
        f_main: ImageFont.ImageFont,
        f_small: ImageFont.ImageFont,
        show_vs: bool = True,
    ) -> None:
        """
        Draws a match card at pixel coords. Reused by full bracket and current-round view.
        Uses your existing style (incl seed badge). Orb is intentionally not drawn.
        """
        style = self.style

        # Local scale helper is unnecessary here (already pixels),
        # but we still need consistent radii/widths. We derive from font size roughly.
        # This stays stable across views.
        radius = max(10, int(h * 0.06))
        border_w = max(2, int(radius * 0.18))

        header_h = max(26, int(h * 0.16))
        inner_gap = max(8, int(h * 0.06))
        team_h = (h - header_h - inner_gap) // 2

        # card background (subtle gradient)
        top = style.box_fill_top
        bot = style.box_fill_bottom
        bands = 10
        for i in range(bands):
            t = i / max(1, bands - 1)
            col = (
                int(top[0] * (1 - t) + bot[0] * t),
                int(top[1] * (1 - t) + bot[1] * t),
                int(top[2] * (1 - t) + bot[2] * t),
                235,
            )
            y0 = y + int((h * i) / bands)
            y1 = y + int((h * (i + 1)) / bands)
            draw.rectangle([x, y0, x + w, y1], fill=col)

        # outer border + inner bevel
        draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, outline=(*style.box_border, 255), width=border_w)
        draw.rounded_rectangle(
            [x + border_w, y + border_w, x + w - border_w, y + h - border_w],
            radius=max(6, radius - 1),
            outline=(*style.box_inner_border, 255),
            width=max(1, border_w // 2),
        )

        # header strip
        hx0, hy0, hx1, hy1 = x, y, x + w, y + header_h
        draw.rectangle([hx0, hy0, hx1, hy1], fill=(8, 8, 10, 220))
        draw.rectangle([hx0, hy0, hx1, hy1], outline=(*style.box_border, 220), width=max(1, border_w // 2))

        # ember notch
        notch_w = max(8, int(w * 0.02))
        draw.rectangle([hx0, hy0, hx0 + notch_w, hy1], fill=(*style.ember, 140))

        # header text
        draw.text((x + max(10, int(w * 0.03)), y + max(3, int(header_h * 0.18))), header, font=f_small, fill=style.subtle)

        def ellipsize(text: str, font: ImageFont.ImageFont, max_w: int) -> str:
            t0 = (text or "").strip()
            if not t0:
                return ""
            if draw.textlength(t0, font=font) <= max_w:
                return t0
            t = t0
            while t and draw.textlength(t + "…", font=font) > max_w:
                t = t[:-1]
            return (t + "…") if t else "…"

        def draw_team_panel(py: int, team_text: str, seed_no: Optional[int], is_winner: bool) -> None:
            # winner glow strip
            if is_winner:
                glow_h = max(6, int(team_h * 0.10))
                draw.rectangle(
                    [x + border_w, py + border_w, x + w - border_w, py + border_w + glow_h],
                    fill=(*style.winner_gold, 85),
                )

            # seed badge (top-left)
            if seed_no is not None:
                badge_w = max(54, int(w * 0.13))
                badge_h = max(24, int(team_h * 0.18))
                bx0 = x + max(10, int(w * 0.03))
                by0 = py + max(8, int(team_h * 0.08))
                bx1 = bx0 + badge_w
                by1 = by0 + badge_h
                badge_fill = (*style.blood, 170) if not is_winner else (*style.winner_green, 170)
                draw.rounded_rectangle([bx0, by0, bx1, by1], radius=max(8, badge_h // 2), fill=badge_fill, outline=(*style.box_border, 220), width=max(1, border_w // 2))
                seed_txt = f"#{int(seed_no)}"
                tw = int(draw.textlength(seed_txt, font=f_small))
                tx = bx0 + max(6, (badge_w - tw) // 2)
                ty = by0 + 2
                draw.text((tx, ty), seed_txt, font=f_small, fill=style.text)

            # inner name slots (two lines)
            slot_x0 = x + max(56, int(w * 0.14))
            slot_x1 = x + w - max(18, int(w * 0.05))
            slot_h = max(22, int(team_h * 0.28))
            slot_gap = max(8, int(team_h * 0.08))

            slot1_y0 = py + max(10, int((team_h - (2 * slot_h + slot_gap)) / 2))
            slot2_y0 = slot1_y0 + slot_h + slot_gap

            draw.rounded_rectangle([slot_x0, slot1_y0, slot_x1, slot1_y0 + slot_h], radius=max(8, slot_h // 3), fill=(10, 10, 12, 190))
            draw.rounded_rectangle([slot_x0, slot2_y0, slot_x1, slot2_y0 + slot_h], radius=max(8, slot_h // 3), fill=(10, 10, 12, 190))
            draw.rounded_rectangle([slot_x0, slot1_y0, slot_x1, slot1_y0 + slot_h], radius=max(8, slot_h // 3), outline=(*style.box_inner_border, 200), width=max(1, border_w // 2))
            draw.rounded_rectangle([slot_x0, slot2_y0, slot_x1, slot2_y0 + slot_h], radius=max(8, slot_h // 3), outline=(*style.box_inner_border, 200), width=max(1, border_w // 2))

            # names
            p1, p2 = self._split_players(team_text)
            max_text_w = (slot_x1 - slot_x0) - 14
            p1 = ellipsize(p1, f_main, max_text_w)
            p2 = ellipsize(p2, f_main, max_text_w)

            text_x = slot_x0 + 10
            t1_y = slot1_y0 + max(2, int((slot_h - (self.style.font_size * self.style.scale)) / 4))
            t2_y = slot2_y0 + max(2, int((slot_h - (self.style.font_size * self.style.scale)) / 4))

            fill1 = style.text if not is_winner else (255, 245, 225)
            draw.text((text_x, t1_y), p1, font=f_main, fill=fill1)
            draw.text((text_x, t2_y), p2, font=f_main, fill=style.text)

        top_y = y + header_h
        bot_y = top_y + team_h + inner_gap

        draw_team_panel(top_y, top_text, top_seed, top_is_winner)
        draw_team_panel(bot_y, bot_text, bot_seed, bot_is_winner)

        if show_vs:
            vs_y = top_y + team_h + (inner_gap // 2) - 10
            draw.text((x + w - 46, vs_y), "VS", font=f_small, fill=style.subtle)

    # -----------------------------
    # Public: full bracket PNG (your existing output)
    # -----------------------------
    def render_png(
        self,
        *,
        event_id: int,
        event_format: str,
        teams_by_seed: Mapping[int, Mapping[str, Any]],
        matches: list[Mapping[str, Any]],
        title: str | None = None,
    ) -> bytes:
        nodes, event_team_id_to_seed, team_count, bracket_size, k, is_double, losers_rounds = self._build_nodes(
            event_format=event_format,
            teams_by_seed=teams_by_seed,
            matches=matches,
        )

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

        img = self._bg_canvas(width=width, height=height)
        draw = ImageDraw.Draw(img)

        f_main = self._font(max(10, int(style.font_size * s)))
        f_small = self._font(max(10, int(style.font_size_small * s)))

        # Helper for edges (scaled from logical to pixels)
        box_w = S(box_w_l)
        box_h = S(box_h_l)

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

        # Title
        if title:
            draw.text((S(margin_l), S(10)), title, font=f_main, fill=style.text)

        # Edges
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

        # Boxes
        def draw_box(x_l: int, y_l: int, node: BracketNode) -> None:
            x = S(x_l)
            y = S(y_l)

            status = (node.status or "pending").lower()
            hdr = f"{node.code}  {status.upper()}"

            t1_text, t1_seed = self._team_label_and_seed(
                seed=node.seed1,
                event_team_id=node.team1_event_team_id,
                event_team_id_to_seed=event_team_id_to_seed,
                teams_by_seed=teams_by_seed,
                team_count=team_count,
            )
            t2_text, t2_seed = self._team_label_and_seed(
                seed=node.seed2,
                event_team_id=node.team2_event_team_id,
                event_team_id_to_seed=event_team_id_to_seed,
                teams_by_seed=teams_by_seed,
                team_count=team_count,
            )

            top_is_winner = (node.winner_event_team_id is not None and node.team1_event_team_id == node.winner_event_team_id)
            bot_is_winner = (node.winner_event_team_id is not None and node.team2_event_team_id == node.winner_event_team_id)

            self._draw_match_card(
                draw=draw,
                x=x,
                y=y,
                w=box_w,
                h=box_h,
                header=hdr,
                top_text=t1_text,
                top_seed=t1_seed,
                top_is_winner=top_is_winner,
                bot_text=t2_text,
                bot_seed=t2_seed,
                bot_is_winner=bot_is_winner,
                f_main=f_main,
                f_small=f_small,
                show_vs=True,
            )

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

        # Bottom-right Teams panel (Seed / Team / Players / W-L)
        stats = self._compute_wl(teams_by_seed=teams_by_seed, matches=matches)

        # scaled panel values
        panel_pad = S(14)
        row_h = S(26)
        hdr_h = S(34)

        col_seed = S(60)
        col_team = S(150)
        col_players = S(600)
        col_wl = S(90)

        panel_w = panel_pad * 2 + col_seed + col_team + col_players + col_wl
        rows = max(1, team_count)
        panel_h = panel_pad * 2 + hdr_h + (rows * row_h)

        px0 = max(S(margin_l), width - panel_w - S(margin_l))
        py0 = max(S(margin_l), height - panel_h - S(margin_l))
        px1 = px0 + panel_w
        py1 = py0 + panel_h

        draw.rounded_rectangle([px0, py0, px1, py1], radius=S(12), fill=(8, 8, 10, 205), outline=(*style.box_border, 230), width=max(2, S(2)))
        draw.rounded_rectangle([px0 + S(2), py0 + S(2), px1 - S(2), py1 - S(2)], radius=S(11), outline=(*style.box_inner_border, 210), width=max(1, S(1)))

        draw.text((px0 + panel_pad, py0 + S(6)), "Teams", font=f_main, fill=style.text)

        hy = py0 + panel_pad + S(34)
        hx = px0 + panel_pad
        draw.rectangle([px0 + S(6), hy - S(6), px1 - S(6), hy + row_h - S(4)], fill=(10, 10, 12, 190))
        draw.text((hx + S(6), hy), "Seed", font=f_small, fill=style.subtle)
        draw.text((hx + col_seed + S(6), hy), "Team", font=f_small, fill=style.subtle)
        draw.text((hx + col_seed + col_team + S(6), hy), "Players", font=f_small, fill=style.subtle)
        draw.text((hx + col_seed + col_team + col_players + S(6), hy), "W-L", font=f_small, fill=style.subtle)

        def ellipsize(text: str, font: ImageFont.ImageFont, max_w: int) -> str:
            t = (text or "").strip()
            if not t:
                return ""
            if draw.textlength(t, font=font) <= max_w:
                return t
            while t and draw.textlength(t + "…", font=font) > max_w:
                t = t[:-1]
            return (t + "…") if t else "…"

        y = hy + row_h
        for seed in sorted(teams_by_seed.keys(), key=lambda z: int(z)):
            t = teams_by_seed.get(int(seed)) or {}
            name = str(t.get("display_name") or t.get("name") or f"Seed {seed}")
            p1, p2 = self._split_players(name)
            players = (p1 + (" + " + p2 if p2 else "")).strip()

            etid = t.get("event_team_id")
            wl = "0-0"
            if etid is not None:
                st = stats.get(int(etid), {"w": 0, "l": 0})
                wl = f"{st.get('w', 0)}-{st.get('l', 0)}"

            seed_txt = f"#{int(seed)}"
            name_fit = ellipsize(name, f_small, col_team - S(14))
            players_fit = ellipsize(players, f_small, col_players - S(14))

            if (int(seed) % 2) == 0:
                draw.rectangle([px0 + S(6), y - S(2), px1 - S(6), y + row_h - S(2)], fill=(12, 10, 12, 140))

            draw.text((hx + S(6), y), seed_txt, font=f_small, fill=style.text)
            draw.text((hx + col_seed + S(6), y), name_fit, font=f_small, fill=style.text)
            draw.text((hx + col_seed + col_team + S(6), y), players_fit, font=f_small, fill=style.text)
            draw.text((hx + col_seed + col_team + col_players + S(6), y), wl, font=f_small, fill=style.text)

            y += row_h

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()

    # -----------------------------
    # Public: current round PNG (easy-to-read match cards)
    # -----------------------------
    def render_current_round_png(
        self,
        *,
        event_id: int,
        event_format: str,
        teams_by_seed: Mapping[int, Mapping[str, Any]],
        matches: list[Mapping[str, Any]],
        title: str | None = None,
        # If you only want "open" matches, set statuses=("open","pending")
        statuses: tuple[str, ...] = ("open", "pending"),
        # Show max cards (safety to avoid massive images)
        max_cards: int = 24,
        # Cards per row (default auto)
        cards_per_row: int | None = None,
    ) -> bytes:
        """
        Generates a compact "current matches" image:
          - Side-by-side match cards (grid)
          - Reuses the same match-card renderer as the full bracket
        You can wire a new Discord command to call this method.
        """
        nodes, event_team_id_to_seed, team_count, _bracket_size, _k, _is_double, _losers_rounds = self._build_nodes(
            event_format=event_format,
            teams_by_seed=teams_by_seed,
            matches=matches,
        )

        style = self.style
        s = float(getattr(style, "scale", 1.0) or 1.0)

        # Scaled fonts
        f_main = self._font(max(10, int(style.font_size * s)))
        f_small = self._font(max(10, int(style.font_size_small * s)))

        # Pick matches that are "current"
        wanted = {str(x).lower() for x in statuses}

        # Keep order stable: Winners first (W), Losers (L), GF last; then round, match.
        def sort_key(n: BracketNode) -> tuple[int, int, int]:
            b = str(n.bracket.value if hasattr(n.bracket, "value") else n.bracket)
            # normalize bracket
            b0 = str(b).upper()
            pri = 0 if b0.startswith("W") else 1 if b0.startswith("L") else 2
            return (pri, int(n.round_no or 0), int(n.match_no or 0))

        current_nodes: list[BracketNode] = []
        for n in nodes.values():
            st = str(n.status or "pending").lower()
            if st in wanted:
                # must have at least one team to be useful
                if (n.team1_event_team_id is not None) or (n.team2_event_team_id is not None) or (n.seed1 is not None) or (n.seed2 is not None):
                    current_nodes.append(n)

        current_nodes.sort(key=sort_key)
        current_nodes = current_nodes[: max(0, int(max_cards))]

        # Layout grid
        margin = int(style.margin * s)
        card_w = int(style.box_w * s)
        card_h = int(style.box_h * s)

        gap_x = int(max(18, style.v_gap * s))   # horizontal gap
        gap_y = int(max(18, style.v_gap * s))   # vertical gap

        if cards_per_row is None:
            # heuristic: 2 or 3 across depending on scale/card size
            cards_per_row = 2 if card_w >= 600 else 3
        cards_per_row = max(1, int(cards_per_row))

        n_cards = len(current_nodes)
        if n_cards == 0:
            # Render a small "no current matches" slate
            width = max(600, margin * 2 + card_w)
            height = max(240, margin * 2 + int(card_h * 0.8))
            img = self._bg_canvas(width=width, height=height)
            draw = ImageDraw.Draw(img)
            msg_title = title or "Current Matches"
            draw.text((margin, margin), msg_title, font=f_main, fill=style.text)
            draw.text((margin, margin + int(46 * s)), "No active matches found.", font=f_small, fill=style.subtle)
            buf = BytesIO()
            img.save(buf, format="PNG", optimize=False)
            return buf.getvalue()

        rows = int(ceil(n_cards / cards_per_row))
        width = margin * 2 + (cards_per_row * card_w) + ((cards_per_row - 1) * gap_x)
        header_room = int(max(70, 70 * s))
        height = margin * 2 + header_room + (rows * card_h) + ((rows - 1) * gap_y)

        img = self._bg_canvas(width=width, height=height)
        draw = ImageDraw.Draw(img)

        # Title
        if title is None:
            title = "Current Matches"
        draw.text((margin, margin), title, font=f_main, fill=style.text)

        # Small legend
        legend_y = margin + int(42 * s)
        draw.text((margin, legend_y), f"Showing: {', '.join(statuses)}", font=f_small, fill=style.subtle)

        # Draw cards
        start_y = margin + header_room
        for idx, node in enumerate(current_nodes):
            row = idx // cards_per_row
            col = idx % cards_per_row
            x = margin + col * (card_w + gap_x)
            y = start_y + row * (card_h + gap_y)

            status = (node.status or "pending").lower()
            hdr = f"{node.code}  {status.upper()}"

            t1_text, t1_seed = self._team_label_and_seed(
                seed=node.seed1,
                event_team_id=node.team1_event_team_id,
                event_team_id_to_seed=event_team_id_to_seed,
                teams_by_seed=teams_by_seed,
                team_count=team_count,
            )
            t2_text, t2_seed = self._team_label_and_seed(
                seed=node.seed2,
                event_team_id=node.team2_event_team_id,
                event_team_id_to_seed=event_team_id_to_seed,
                teams_by_seed=teams_by_seed,
                team_count=team_count,
            )

            top_is_winner = (node.winner_event_team_id is not None and node.team1_event_team_id == node.winner_event_team_id)
            bot_is_winner = (node.winner_event_team_id is not None and node.team2_event_team_id == node.winner_event_team_id)

            self._draw_match_card(
                draw=draw,
                x=x,
                y=y,
                w=card_w,
                h=card_h,
                header=hdr,
                top_text=t1_text,
                top_seed=t1_seed,
                top_is_winner=top_is_winner,
                bot_text=t2_text,
                bot_seed=t2_seed,
                bot_is_winner=bot_is_winner,
                f_main=f_main,
                f_small=f_small,
                show_vs=True,
            )

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()
