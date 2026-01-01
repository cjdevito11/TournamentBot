# renderers/embeds.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import discord


@dataclass(frozen=True)
class EmbedTheme:
    primary: int = 0xB08D57   # antique gold
    success: int = 0x2ECC71
    warning: int = 0xF1C40F
    danger: int = 0xE74C3C
    neutral: int = 0x5865F2   # discord-ish blue


class Embeds:
    """
    Centralized embed styling so every command looks consistent.
    Also includes small helpers for consistent "how to use" hints.
    """

    def __init__(self, *, theme: EmbedTheme | None = None, footer: str = "D2 Hustlers") -> None:
        self._theme = theme or EmbedTheme()
        self._footer = footer

    def base(
        self,
        *,
        title: str,
        description: str | None = None,
        color: int | None = None,
        url: str | None = None,
    ) -> discord.Embed:
        e = discord.Embed(
            title=title,
            description=description,
            color=color if color is not None else self._theme.primary,
            url=url,
        )
        e.set_footer(text=self._footer)
        return e

    def info(self, *, title: str, description: str | None = None) -> discord.Embed:
        return self.base(title=title, description=description, color=self._theme.neutral)

    def success(self, *, title: str, description: str | None = None) -> discord.Embed:
        return self.base(title=title, description=description, color=self._theme.success)

    def warning(self, *, title: str, description: str | None = None) -> discord.Embed:
        return self.base(title=title, description=description, color=self._theme.warning)

    def error(self, *, title: str, description: str | None = None) -> discord.Embed:
        return self.base(title=title, description=description, color=self._theme.danger)

    def field_kv(
        self,
        embed: discord.Embed,
        *,
        name: str,
        value: str,
        inline: bool = False,
    ) -> discord.Embed:
        embed.add_field(name=name, value=value, inline=inline)
        return embed

    # -------------------------
    # Formatting helpers
    # -------------------------

    def small_code(self, text: str, lang: str = "") -> str:
        lang = (lang or "").strip()
        return f"```{lang}\n{text}\n```"

    def cmd(self, text: str) -> str:
        """
        Formats a command inline consistently.
        """
        t = (text or "").strip()
        return f"`{t}`" if t else "``"

    def mention_user(self, user_id: int) -> str:
        return f"<@{int(user_id)}>"

    def mention_channel(self, channel_id: int) -> str:
        return f"<#{int(channel_id)}>"

    # -------------------------
    # Tournament hint helpers
    # -------------------------

    def report_syntax(
        self,
        *,
        event_id: int | None = None,
        match_code: str = "W1-01",
        winner_seed: int = 1,
    ) -> str:
        """
        Canonical reporting syntax (seed-based).
        Example:
          /event report event_id:1 match_code:W1-01 winner_seed:3
        """
        eid = "EVENT_ID" if event_id is None else str(int(event_id))
        return f"/event report event_id:{eid} match_code:{match_code} winner_seed:{int(winner_seed)}"

    def add_report_hint(
        self,
        embed: discord.Embed,
        *,
        event_id: int | None = None,
        example_match_code: str = "W1-01",
        example_winner_seed: int = 1,
        inline: bool = False,
    ) -> discord.Embed:
        """
        Adds a single standardized field telling users how to report results.
        Use this in:
          - /event bracket output embed
          - post-bracket-creation confirmation
          - error embeds when report input is invalid
        """
        syntax = self.report_syntax(
            event_id=event_id,
            match_code=example_match_code,
            winner_seed=example_winner_seed,
        )

        value = (
            f"{self.cmd(syntax)}\n"
            f"Match codes look like `{example_match_code}` (W/L rounds) or `GF-01` (Grand Finals)."
        )
        embed.add_field(name="Report result", value=value, inline=inline)
        return embed
