"""
utils/steam_api.py — Парсинг SteamID всех форматов + запросы к Steam Web API.

Поддерживаемые форматы входной строки:
    - SteamID64:            76561198000000000
    - SteamID32 / Steam2:   STEAM_0:1:11101
                            STEAM_1:1:11101
    - Steam3:               [U:1:22202]
                            U:1:22202
    - Profile URL:          https://steamcommunity.com/profiles/76561198000000000/
    - Vanity URL:           https://steamcommunity.com/id/vanityname
                            vanityname
                            id/vanityname

Steam Web API endpoints:
    - ISteamUser/ResolveVanityURL/v1/
    - ISteamUser/GetPlayerSummaries/v2/
    - ISteamUser/GetPlayerBans/v1/
    - IPlayerService/GetOwnedGames/v1/
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

STEAM_API_BASE = "https://api.steampowered.com"
RUST_APP_ID = 252490

# Константы конвертации SteamID
STEAM64_BASE = 76561197960265728  # 0x0110000100000000


# ============================================================================
# Парсинг входной строки -> steamid64
# ============================================================================

def _steam2_to_steam64(steam2: str) -> Optional[int]:
    """STEAM_X:Y:Z -> steamid64."""
    m = re.match(r"^STEAM_[0-5]:([01]):(\d+)$", steam2.strip())
    if not m:
        return None
    y, z = int(m.group(1)), int(m.group(2))
    return STEAM64_BASE + z * 2 + y


def _steam3_to_steam64(steam3: str) -> Optional[int]:
    """[U:1:N] или U:1:N -> steamid64."""
    s = steam3.strip()
    m = re.match(r"^\[?U:1:(\d+)\]?$", s)
    if not m:
        return None
    n = int(m.group(1))
    return STEAM64_BASE + n


def _maybe_steam64(s: str) -> Optional[int]:
    """Если строка — это 17-значное число, начинающееся с 765611..."""
    s = s.strip()
    if re.fullmatch(r"7656119\d{10}", s):
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _extract_profile_id(url: str) -> Optional[int]:
    """/profiles/765611... -> steamid64."""
    m = re.search(r"/profiles/(\d{17})", url)
    if m:
        return int(m.group(1))
    return None


def _extract_vanity_name(url_or_name: str) -> Optional[str]:
    """Возвращает vanity name из URL или из чистой строки."""
    s = url_or_name.strip().rstrip("/")
    m = re.search(r"/id/([^/?#]+)", s)
    if m:
        return m.group(1)
    # Если это просто слово без /, пробуем как vanity
    if re.fullmatch(r"[A-Za-z0-9_\-.]{2,32}", s) and " " not in s and "." not in s:
        return s
    return None


def parse_steamid(raw: str) -> tuple[Optional[int], Optional[str]]:
    """
    Возвращает кортеж (steamid64, vanity_name).
    Один из элементов будет None:
        - Если сразу распарсили в steamid64 — vanity_name = None.
        - Если это vanity URL/имя — steamid64 = None, vanity_name заполнен.
    """
    if not raw or not isinstance(raw, str):
        return None, None

    s = raw.strip()

    # 1. SteamID64 (просто число)
    sid = _maybe_steam64(s)
    if sid:
        return sid, None

    # 2. Steam2
    sid = _steam2_to_steam64(s)
    if sid:
        return sid, None

    # 3. Steam3
    sid = _steam3_to_steam64(s)
    if sid:
        return sid, None

    # 4. URL с /profiles/
    sid = _extract_profile_id(s)
    if sid:
        return sid, None

    # 5. URL с /id/ или чистое vanity
    vanity = _extract_vanity_name(s)
    if vanity:
        return None, vanity

    return None, None


# ============================================================================
# Steam Web API
# ============================================================================

async def _api_get(session: aiohttp.ClientSession, endpoint: str,
                   params: dict) -> dict:
    url = f"{STEAM_API_BASE}/{endpoint}"
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                log.warning("Steam API %s returned %s", endpoint, resp.status)
                return {}
            data = await resp.json(content_type=None)
            return data.get("response", {}) or {}
    except asyncio.TimeoutError:
        log.warning("Steam API %s timeout", endpoint)
        return {}
    except Exception as e:
        log.warning("Steam API %s error: %s", endpoint, e)
        return {}


async def resolve_vanity(session: aiohttp.ClientSession, api_key: str,
                         vanity: str) -> Optional[int]:
    data = await _api_get(
        session,
        "ISteamUser/ResolveVanityURL/v1/",
        {"key": api_key, "vanityurl": vanity},
    )
    if data.get("success") == 1 and data.get("steamid"):
        return int(data["steamid"])
    return None


async def get_player_summaries(session: aiohttp.ClientSession, api_key: str,
                               steamid: int) -> dict:
    data = await _api_get(
        session,
        "ISteamUser/GetPlayerSummaries/v2/",
        {"key": api_key, "steamids": str(steamid)},
    )
    players = data.get("players") or []
    return players[0] if players else {}


async def get_player_bans(session: aiohttp.ClientSession, api_key: str,
                          steamid: int) -> dict:
    data = await _api_get(
        session,
        "ISteamUser/GetPlayerBans/v1/",
        {"key": api_key, "steamids": str(steamid)},
    )
    players = data.get("players") or []
    return players[0] if players else {}


async def get_owned_games(session: aiohttp.ClientSession, api_key: str,
                          steamid: int) -> dict:
    """Возвращает словарь игры (по appid) -> {playtime_forever_мин}."""
    data = await _api_get(
        session,
        "IPlayerService/GetOwnedGames/v1/",
        {"key": api_key, "steamid": str(steamid),
         "include_appinfo": 1, "include_played_free_games": 0},
    )
    games = data.get("games") or []
    return {g["appid"]: g for g in games}


# ============================================================================
# Главная функция проверки Steam-аккаунта
# ============================================================================

async def check_steam_account(api_key: str, raw_input: str) -> dict:
    """
    Универсальная проверка Steam-аккаунта.

    Возвращает dict с полями:
        success: bool
        steamid: int | None
        profile_url: str | None
        persona: str | None
        avatar: str | None
        vac_banned: bool
        community_banned: bool
        hours_rust: float | None          (None если скрыто/нет игры)
        profile_state: str                ('public' | 'private' | 'unknown')
        error: str | None
    """
    result = {
        "success": False,
        "steamid": None,
        "profile_url": None,
        "persona": None,
        "avatar": None,
        "vac_banned": False,
        "community_banned": False,
        "hours_rust": None,
        "profile_state": "unknown",
        "error": None,
    }

    if not api_key or api_key == "ВСТАВЬ_ТОКЕН_БОТА":
        result["error"] = "Steam API ключ не настроен"
        return result

    sid, vanity = parse_steamid(raw_input)
    if sid is None and vanity is None:
        result["error"] = "Не удалось распознать SteamID или ссылку"
        return result

    async with aiohttp.ClientSession() as session:
        # Если vanity — резолвим
        if sid is None and vanity:
            sid = await resolve_vanity(session, api_key, vanity)
            if sid is None:
                result["error"] = f"Не удалось найти профиль по vanity '{vanity}'"
                return result

        # Параллельно: summaries, bans, owned games
        summaries_task = get_player_summaries(session, api_key, sid)
        bans_task = get_player_bans(session, api_key, sid)
        games_task = get_owned_games(session, api_key, sid)

        summaries, bans, games = await asyncio.gather(
            summaries_task, bans_task, games_task
        )

    result["steamid"] = sid
    result["profile_url"] = f"https://steamcommunity.com/profiles/{sid}"

    if summaries:
        result["persona"] = summaries.get("personaname")
        result["avatar"] = summaries.get("avatarfull")
        vis = summaries.get("communityvisibilitystate", 0)
        # 1 - private, 2 - friends only, 3 - public
        result["profile_state"] = "private" if vis in (1, 2) else "public"
    else:
        result["profile_state"] = "unknown"

    if bans:
        result["vac_banned"] = bool(bans.get("VACBanned", False))
        result["community_banned"] = bool(bans.get("CommunityBanned", False))

    # Часы в Rust
    rust = games.get(RUST_APP_ID)
    if rust is not None:
        # playtime_forever в минутах
        result["hours_rust"] = round(rust.get("playtime_forever", 0) / 60, 1)
    # Если профиль приватный — owned games недоступен => hours_rust = None

    result["success"] = True
    return result
