from __future__ import annotations

from io import BytesIO
from typing import Sequence
from PIL import Image, ImageDraw, ImageFont
import random


class SpinRevealRenderer:
    """
    Extremely simple slot / wheel style renderer.
    One highlighted row = current cursor.
    """

    def __init__(self, *, width: int = 900, height: int = 520) -> None:
        self.width = width
        self.height = height

        try:
            self.font = ImageFont.truetype("DejaVuSansMono.ttf", 32)
            self.font_small = ImageFont.truetype("DejaVuSansMono.ttf", 22)
        except Exception:
            self.font = ImageFont.load_default()
            self.font_small = ImageFont.load_default()

    def render_frame(
        self,
        *,
        title: str,
        entries: Sequence[str],
        cursor: int,
        phase: str = "Spinning…",
    ) -> bytes:
        """
        entries: list of names (already shuffled upstream)
        cursor: index to highlight
        """

        img = Image.new("RGBA", (self.width, self.height), (10, 10, 12, 255))
        draw = ImageDraw.Draw(img)

        # Title
        draw.text((24, 18), title, font=self.font, fill=(240, 232, 220))
        draw.text((24, 58), phase, font=self.font_small, fill=(180, 170, 160))

        # Slot window
        panel_x0 = 40
        panel_y0 = 110
        panel_x1 = self.width - 40
        panel_y1 = self.height - 40

        draw.rounded_rectangle(
            [panel_x0, panel_y0, panel_x1, panel_y1],
            radius=18,
            fill=(18, 16, 18, 240),
            outline=(120, 110, 100, 220),
            width=3,
        )

        visible = 9
        mid = visible // 2
        start = max(0, cursor - mid)
        slice_entries = entries[start : start + visible]

        row_h = 36
        cx = (panel_x0 + panel_x1) // 2
        y = panel_y0 + 40

        for i, name in enumerate(slice_entries):
            is_active = (start + i) == cursor

            color = (255, 245, 220) if is_active else (190, 180, 170)
            bg = (80, 40, 40, 180) if is_active else None

            if bg:
                draw.rounded_rectangle(
                    [panel_x0 + 16, y - 4, panel_x1 - 16, y + row_h],
                    radius=10,
                    fill=bg,
                )

            tw = int(draw.textlength(name, font=self.font))
            draw.text((cx - tw // 2, y), name, font=self.font, fill=color)

            y += row_h

        # Center marker
        marker_y = panel_y0 + 40 + (mid * row_h)
        draw.line(
            [(panel_x0 + 10, marker_y + 16), (panel_x1 - 10, marker_y + 16)],
            fill=(214, 112, 38),
            width=3,
        )

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()
