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

    def small_code(self, text: str, lang: str = "") -> str:
        lang = (lang or "").strip()
        return f"```{lang}\n{text}\n```"

    def mention_user(self, user_id: int) -> str:
        return f"<@{int(user_id)}>"

    def mention_channel(self, channel_id: int) -> str:
        return f"<#{int(channel_id)}>"
