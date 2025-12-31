# config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _maybe_load_env_file() -> None:
    """
    Load .env from the project root (same folder as this config.py).

    - If python-dotenv is installed, use it.
    - Otherwise, fall back to a tiny parser.
    - Never overwrites already-set environment variables.
    """
    dotenv_path = Path(__file__).resolve().parent / ".env"
    if not dotenv_path.exists():
        return

    # Prefer python-dotenv if available
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path=dotenv_path, override=False)
        return
    except Exception:
        pass

    # Fallback parser
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass(frozen=True)
class MySqlConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    minsize: int = 1
    maxsize: int = 5
    connect_timeout: int = 10


@dataclass(frozen=True)
class BotConfig:
    token: str
    dev_guild_id: int | None
    default_announce_channel_id: int | None
    command_prefix: str
    log_level: str
    mysql: MySqlConfig


def _getenv(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


def _int_or_none(value: str | None, var_name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as e:
        raise ValueError(f"{var_name} must be an integer, got: {value!r}") from e


def _int(value: str | None, var_name: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as e:
        raise ValueError(f"{var_name} must be an integer, got: {value!r}") from e


def load_config() -> BotConfig:
    _maybe_load_env_file()  # <--- add this line

    token = (_getenv("DISCORD_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

    dev_guild_id = _int_or_none(_getenv("DEV_GUILD_ID"), "DEV_GUILD_ID")
    default_announce_channel_id = _int_or_none(_getenv("ANNOUNCE_CHANNEL_ID"), "ANNOUNCE_CHANNEL_ID")

    host = _getenv("DB_HOST", "127.0.0.1") or "127.0.0.1"
    port = _int(_getenv("DB_PORT"), "DB_PORT", 3306)
    user = _getenv("DB_USER", "root") or "root"
    password = _getenv("DB_PASSWORD", "") or ""
    database = _getenv("DB_NAME", "d2_discord_bot") or "d2_discord_bot"

    minsize = _int(_getenv("DB_POOL_MIN"), "DB_POOL_MIN", 1)
    maxsize = _int(_getenv("DB_POOL_MAX"), "DB_POOL_MAX", 5)
    connect_timeout = _int(_getenv("DB_CONNECT_TIMEOUT"), "DB_CONNECT_TIMEOUT", 10)

    if minsize < 1:
        raise ValueError("DB_POOL_MIN must be >= 1")
    if maxsize < minsize:
        raise ValueError("DB_POOL_MAX must be >= DB_POOL_MIN")

    return BotConfig(
        token=token,
        dev_guild_id=dev_guild_id,
        default_announce_channel_id=default_announce_channel_id,
        command_prefix=_getenv("COMMAND_PREFIX", "!") or "!",
        log_level=(_getenv("LOG_LEVEL", "INFO") or "INFO").upper(),
        mysql=MySqlConfig(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            minsize=minsize,
            maxsize=maxsize,
            connect_timeout=connect_timeout,
        ),
    )
