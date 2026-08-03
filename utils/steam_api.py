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

Два режима проверки:
    1. Steam Web API (если задан STEAM_API_KEY) — получает VAC-баны, playtime_2weeks и т.п.
    2. HTML-парсинг профиля (fallback, без ключа) — извлекает steamid, personaname, avatar,
       статус, страны, даты. Используется когда API ключ невалидный или отсутствует.

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
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp

# BeautifulSoup был нужен для HTML-парсинга профиля Steam (fallback-режим без API-ключа).
# Сейчас бот работает только через Steam Web API, поэтому bs4 не обязателен.
# Импортируем опционально — если библиотека установлена, можно использовать в будущем.
try:  # pragma: no cover
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_COMMUNITY = "https://steamcommunity.com"
RUST_APP_ID = 252490

# Константы конвертации SteamID
STEAM64_BASE = 76561197960265728  # 0x0110000100000000

# МСК для форматирования дат
MSK = timezone(timedelta(hours=3))

# Кэш проверок (steamid64 -> result), TTL ~5 минут
# Чтобы не дёргать Steam повторно при recheck в одном тикете
_CACHE: dict[int, tuple[float, dict]] = {}
_CACHE_TTL = 300  # 5 минут


def _cache_get(sid: int) -> Optional[dict]:
    """Возвращает кэшированный результат если не истёк TTL."""
    import time
    entry = _CACHE.get(sid)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(sid, None)
        return None
    return data


def _cache_set(sid: int, data: dict) -> None:
    import time
    _CACHE[sid] = (time.time(), data)


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
    # Полный URL: https://steamcommunity.com/id/VANITY
    m = re.search(r"/id/([^/?#]+)", s)
    if m:
        return m.group(1)
    # Короткая форма: id/VANITY
    m = re.match(r"^id/([A-Za-z0-9_\-.]{2,32})$", s)
    if m:
        return m.group(1)
    # Просто vanity имя без /
    # Разрешаем буквы/цифры/_-. (стандартные vanity), длина 2-32
    if re.fullmatch(r"[A-Za-z0-9_\-.]{2,32}", s) and " " not in s:
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
    """
    Запрос к Steam Web API с retry-логикой и backoff.

    Возвращает:
        - dict с данными ответа (ключ "response" уже развёрнут) при успехе
        - {"_error": "invalid_api_key"} при 403 (невалидный ключ — не ретраим)
        - {"_error": "rate_limited"} при 429 (rate limit — ретраим с backoff)
        - {"_error": "timeout"} если все попытки провалились по таймауту
        - {"_error": "network"} если сетевая ошибка
        - {} для прочих неуспешных статусов
    """
    url = f"{STEAM_API_BASE}/{endpoint}"
    max_attempts = 3
    base_timeout = 12  # секунд на попытку

    last_error: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=base_timeout),
            ) as resp:
                if resp.status == 403:
                    # 403 = невалидный API key или key заблокирован.
                    # НЕ ретраим — это не временная ошибка.
                    log.error(
                        "Steam API %s: 403 Forbidden — невалидный STEAM_API_KEY. "
                        "Получить новый: https://steamcommunity.com/dev/apikey",
                        endpoint,
                    )
                    return {"_error": "invalid_api_key"}
                if resp.status == 429:
                    # Rate limit — ретраим с экспоненциальным backoff.
                    log.warning(
                        "Steam API %s: 429 (попытка %d/%d)",
                        endpoint, attempt, max_attempts,
                    )
                    last_error = "rate_limited"
                    if attempt < max_attempts:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    return {"_error": "rate_limited"}
                if resp.status >= 500:
                    # Серверная ошибка Steam — ретраим.
                    log.warning(
                        "Steam API %s: %d server error (попытка %d/%d)",
                        endpoint, resp.status, attempt, max_attempts,
                    )
                    last_error = "server_error"
                    if attempt < max_attempts:
                        await asyncio.sleep(1.0 * attempt)
                        continue
                    return {"_error": "server_error"}
                if resp.status != 200:
                    log.warning("Steam API %s returned %s", endpoint, resp.status)
                    return {}
                data = await resp.json(content_type=None)
                return data.get("response", {}) or {}
        except asyncio.TimeoutError:
            log.warning(
                "Steam API %s timeout (попытка %d/%d)",
                endpoint, attempt, max_attempts,
            )
            last_error = "timeout"
            if attempt < max_attempts:
                await asyncio.sleep(1.0 * attempt)
                continue
            return {"_error": "timeout"}
        except aiohttp.ClientError as e:
            # Сетевые ошибки: connection reset, DNS, и т.п. — ретраим.
            log.warning(
                "Steam API %s network error: %s (попытка %d/%d)",
                endpoint, e, attempt, max_attempts,
            )
            last_error = "network"
            if attempt < max_attempts:
                await asyncio.sleep(1.0 * attempt)
                continue
            return {"_error": "network"}
        except Exception as e:
            log.warning("Steam API %s unexpected error: %s", endpoint, e)
            return {"_error": "unknown"}

    # Если дошли сюда — все попытки провалились
    return {"_error": last_error or "unknown"}


async def resolve_vanity(session: aiohttp.ClientSession, api_key: str,
                         vanity: str) -> Optional[int]:
    data = await _api_get(
        session,
        "ISteamUser/ResolveVanityURL/v1/",
        {"key": api_key, "vanityurl": vanity},
    )
    if data.get("_error"):
        # Сигнализируем об ошибке API — она нужна вызывающему коду для fallback
        raise SteamApiError(data["_error"])
    if data.get("success") == 1 and data.get("steamid"):
        return int(data["steamid"])
    return None


class SteamApiError(Exception):
    """Сигнальная ошибка — Steam API недоступен, нужен fallback."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
# HTML Fallback: парсинг профиля напрямую (без Steam API ключа)
# ============================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Скачивает HTML страницы профиля."""
    try:
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                log.warning("HTML fetch %s returned %s", url, resp.status)
                return None
            return await resp.text(errors="replace")
    except asyncio.TimeoutError:
        log.warning("HTML fetch %s timeout", url)
        return None
    except Exception as e:
        log.warning("HTML fetch %s error: %s", url, e)
        return None


def _parse_profile_html(html: str, profile_url: str) -> dict:
    """
    Извлекает данные из HTML-страницы Steam-профиля.
    Возвращает dict с полями:
        steamid, persona, avatar, profile_url, profile_state, last_logoff,
        time_created, country_code, currently_playing, online_status, summary
    """
    result = {
        "steamid": None,
        "persona": None,
        "avatar": None,
        "profile_url": profile_url,
        "profile_state": "unknown",
        "last_logoff": None,
        "time_created": None,
        "country_code": None,
        "currently_playing": None,
        "online_status": "unknown",
        "summary": None,
    }

    # 1. SteamID64 — embedded в JS data
    m = re.search(r'"steamid":"(\d{17})"', html)
    if m:
        result["steamid"] = int(m.group(1))
    else:
        # Fallback: g_steamID = "76561199..."
        m = re.search(r'g_steamID\s*=\s*"?(\d{17})"?', html)
        if m:
            result["steamid"] = int(m.group(1))

    # 2. Persona name
    m = re.search(r'"personaname":"([^"]+)"', html)
    if m:
        try:
            result["persona"] = m.group(1).encode().decode('unicode_escape', errors='replace')
        except Exception:
            result["persona"] = m.group(1)
    else:
        m = re.search(r'class="actual_persona_name"[^>]*>\s*([^<]+)<', html)
        if m:
            result["persona"] = m.group(1).strip()

    # 3. Avatar (полный размер) — через playerAvatar div
    avatar_patterns = [
        # Сначала пробуем playerAvatarAutoSizeInner
        r'<div[^>]*class="playerAvatarAutoSizeInner[^"]*"[^>]*>\s*<img[^>]*src="([^"]+)"',
        # Затем playerAvatar
        r'<div[^>]*class="playerAvatar[^"]*"[^>]*>\s*<img[^>]*src="([^"]+)"',
        # Fallback — любой avatar URL
        r'<img[^>]*src="(https://avatars\.fastly\.steamstatic\.com/[^"]+)"',
        r'<img[^>]*src="(https://avatars\.steamstatic\.com/[^"]+)"',
    ]
    for p in avatar_patterns:
        m = re.search(p, html)
        if m:
            url = m.group(1)
            # Steam хранит аватары в 3 размерах: _medium, _full, без суффикса
            # Для embed нужен _full
            if "_medium" in url:
                url = url.replace("_medium", "_full")
            result["avatar"] = url
            break

    # 4. Online-статус (через profile_in_game класс)
    # Steam использует: profile_in_game persona offline / online / in-game
    if 'profile_in_game persona in-game' in html:
        result["online_status"] = "in-game"
        # Ищем название игры
        m = re.search(r'profile_in_game_header[^>]*>\s*Currently In-Game\s*</div>\s*<div[^>]*>([^<]+)<', html)
        if m:
            result["currently_playing"] = m.group(1).strip()
    elif 'profile_in_game persona online' in html:
        result["online_status"] = "online"
    elif 'profile_in_game persona offline' in html:
        result["online_status"] = "offline"
    # Also try to extract "Currently In-Game" game title
    if not result["currently_playing"]:
        m = re.search(r'Currently In-Game[^<]*</div>\s*<div[^>]*class="[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>', html)
        if m:
            result["currently_playing"] = m.group(1).strip()

    # 5. Profile visibility
    # Steam обычно показывает profile_private_info_text если профиль приватный
    if 'profile_private_info' in html or 'This profile is Private' in html:
        result["profile_state"] = "private"
    elif 'profile_private' in html:
        result["profile_state"] = "private"
    elif result["persona"]:  # Если есть persona name — профиль как минимум виден
        result["profile_state"] = "public"
    # communityvisibilitystate может быть в JS data
    m = re.search(r'"communityvisibilitystate":(\d+)', html)
    if m:
        vis = int(m.group(1))
        result["profile_state"] = "private" if vis in (1, 2) else "public"

    # 6. Last logoff (не всегда доступно в HTML)
    m = re.search(r'"lastlogoff":(\d+)', html)
    if m:
        result["last_logoff"] = int(m.group(1))

    # 7. Account creation date (не всегда доступно в HTML)
    m = re.search(r'"timecreated":(\d+)', html)
    if m:
        result["time_created"] = int(m.group(1))

    # 8. Country (не всегда доступно)
    m = re.search(r'"loccountrycode":"([A-Z]{2})"', html)
    if m:
        result["country_code"] = m.group(1)

    return result


async def _scrape_profile(session: aiohttp.ClientSession,
                          steamid: int) -> Optional[dict]:
    """Парсит профиль по steamid64 напрямую (HTML)."""
    url = f"{STEAM_COMMUNITY}/profiles/{steamid}"
    html = await _fetch_html(session, url)
    if not html:
        return None
    return _parse_profile_html(html, url)


async def _scrape_vanity(session: aiohttp.ClientSession,
                         vanity: str) -> Optional[dict]:
    """Парсит профиль по vanity name напрямую (HTML)."""
    url = f"{STEAM_COMMUNITY}/id/{vanity}"
    html = await _fetch_html(session, url)
    if not html:
        return None
    return _parse_profile_html(html, url)


async def _scrape_rust_hours(session: aiohttp.ClientSession,
                              steamid: int) -> Optional[float]:
    """
    Парсит страницу /games/{steamid} для поиска Rust (appid 252490).
    ⚠️ Steam требует авторизацию для просмотра /games/ — обычно возвращает
    страницу логина. Этот метод сработает только если у пользователя
    публичный профиль И Steam передаёт игры в JS.
    Возвращает часы в Rust (float) или None если не найдено.
    """
    url = f"{STEAM_COMMUNITY}/profiles/{steamid}/games/?tab=all"
    html = await _fetch_html(session, url)
    if not html:
        return None

    # Если вернулась страница логина — выходим
    if '<title>Sign In</title>' in html or 'Please log in' in html:
        log.info("Steam /games/ требует логин — Rust playtime недоступен")
        return None

    # Steam embeds all games as JSON in rgGames JS variable
    # Pattern: {"appid":252490,"name":"Rust","logo":...,"hours_forever":"123.4",...}
    patterns = [
        r'\{"appid":252490[^}]*"hours_forever":"([\d.,]+)"',
        r'\{"appid":252490[^}]*"hours_forever":([\d.]+)',
        r'\{"appid":252490[^}]*"playtime_forever":(\d+)',
        r'"appid":252490,"name":"Rust"[^}]*"hours_forever":"([\d.,]+)"',
    ]
    for p in patterns:
        m = re.search(p, html, re.S)
        if m:
            val = m.group(1).replace(",", ".")
            try:
                hours = float(val)
                if hours > 100000:
                    return round(hours / 60, 1)
                return round(hours, 1)
            except ValueError:
                continue

    return None


# ============================================================================
# Главная функция проверки Steam-аккаунта
# ============================================================================

def _format_unix(ts: Optional[int]) -> str:
    """Форматирует UNIX timestamp в читаемую дату МСК, или '—' если None."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromtimestamp(ts, tz=MSK)
        return dt.strftime("%d.%m.%Y")
    except (OSError, ValueError):
        return "—"


def _format_last_seen(ts: Optional[int]) -> str:
    """Форматирует 'последний заход' дружелюбно."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromtimestamp(ts, tz=MSK)
        now = datetime.now(tz=MSK)
        delta = now - dt
        if delta.days == 0:
            hours = int(delta.total_seconds() // 3600)
            if hours <= 0:
                return "только что"
            return f"сегодня, {hours} ч. назад"
        elif delta.days == 1:
            return "вчера"
        elif delta.days < 7:
            return f"{delta.days} дн. назад"
        elif delta.days < 30:
            return f"~{delta.days // 7} нед. назад"
        elif delta.days < 365:
            return f"~{delta.days // 30} мес. назад"
        else:
            return dt.strftime("%d.%m.%Y")
    except (OSError, ValueError):
        return "—"


async def check_steam_account(api_key: str, raw_input: str) -> dict:
    """
    Универсальная проверка Steam-аккаунта.

    Стратегия:
        1. Парсим входную строку.
        2. Если это vanity URL и есть валидный API ключ — резолвим через API.
        3. Если это vanity URL и API ключ невалидный/отсутствует — парсим HTML.
        4. Запрашиваем данные:
           a) Через Steam Web API (если есть валидный ключ) — VAC-баны, часы в Rust.
           b) Через HTML-парсинг (fallback) — базовая инфа без VAC-банов.
        5. Если API частично упал — комбинируем результаты.

    Возвращает dict с полями:
        success: bool
        steamid: int | None
        profile_url: str | None
        persona: str | None
        avatar: str | None
        vac_banned: bool
        community_banned: bool
        days_since_last_ban: int | None
        hours_rust: float | None          (None если скрыто/нет игры)
        profile_state: str                ('public' | 'private' | 'unknown')
        last_logoff: int | None
        last_seen: str                    (дружелюбная строка)
        time_created: int | None
        account_created: str              (дата создания)
        country_code: str | None
        currently_playing: str | None
        source: str                       ('api' | 'html' | 'mixed')
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
        "days_since_last_ban": None,
        "hours_rust": None,
        "profile_state": "unknown",
        "online_status": "unknown",
        "last_logoff": None,
        "last_seen": "—",
        "time_created": None,
        "account_created": "—",
        "country_code": None,
        "currently_playing": None,
        "source": "unknown",
        "error": None,
    }

    if not raw_input or not isinstance(raw_input, str):
        result["error"] = "Пустой ввод"
        return result

    sid, vanity = parse_steamid(raw_input)
    if sid is None and vanity is None:
        result["error"] = ("Не удалось распознать SteamID или ссылку. "
                           "Поддерживаются: SteamID64, STEAM_X:Y:Z, [U:1:N], "
                           "ссылка на профиль или vanity-имя.")
        return result

    # Используем кастомный HTTP-стек с DNS-резолвером 1.1.1.1 / 8.8.8.8.
    # Это критично для работы в РФ, где системный DNS может быть заблокирован.
    try:
        from utils.http import make_session
        session_ctx = make_session(timeout=30)
    except ImportError:
        session_ctx = aiohttp.ClientSession()
    
    async with session_ctx as session:
        # ── Шаг 1: получаем steamid64 ────────────────────────────────────────
        api_key_valid = bool(api_key and api_key != "ВСТАВЬ_ТОКЕН_БОТА")

        if sid is None and vanity:
            # Сначала пробуем API (быстрее, точнее)
            if api_key_valid:
                try:
                    sid = await resolve_vanity(session, api_key, vanity)
                except SteamApiError as e:
                    log.info("Steam API недоступен (%s) — переключаемся на HTML-парсинг", e.code)
                    sid = None
                except Exception as e:
                    log.warning("resolve_vanity error: %s", e)
                    sid = None

            # Fallback: HTML-парсинг vanity-страницы
            if sid is None:
                log.info("Парсим vanity '%s' через HTML", vanity)
                scraped = await _scrape_vanity(session, vanity)
                if scraped and scraped.get("steamid"):
                    sid = scraped["steamid"]
                    # Сохраняем уже полученные данные
                    _fill_from_scrape(result, scraped)
                    result["source"] = "html"
                else:
                    result["error"] = (
                        f"Не удалось найти профиль по vanity '{vanity}'. "
                        f"Проверьте правильность ссылки."
                    )
                    return result

        if sid is None:
            result["error"] = "Не удалось определить SteamID"
            return result

        # ── Кэш ───────────────────────────────────────────────────────────────
        cached = _cache_get(sid)
        if cached:
            log.info("Steam проверка: взят из кэша для %s", sid)
            return cached

        # ── Шаг 2: получаем полную инфу ───────────────────────────────────────
        result["steamid"] = sid
        result["profile_url"] = f"https://steamcommunity.com/profiles/{sid}"

        # Если уже получили базовую инфу через HTML-парсинг vanity — дополняем
        scraped_profile = None
        if result["source"] == "html" and result.get("persona"):
            scraped_profile = {
                "persona": result["persona"],
                "avatar": result["avatar"],
                "profile_state": result["profile_state"],
                "last_logoff": result["last_logoff"],
                "time_created": result["time_created"],
                "country_code": result["country_code"],
                "currently_playing": result["currently_playing"],
            }

        # Если ещё нет данных — пробуем API (если ключ валидный)
        used_api = False
        if api_key_valid:
            try:
                summaries_task = get_player_summaries(session, api_key, sid)
                bans_task = get_player_bans(session, api_key, sid)
                games_task = get_owned_games(session, api_key, sid)

                summaries, bans, games = await asyncio.gather(
                    summaries_task, bans_task, games_task
                )

                # Проверяем ошибки API
                api_failed = False
                for r in (summaries, bans, games):
                    if isinstance(r, dict) and r.get("_error"):
                        err = r["_error"]
                        log.info("Steam API завершился с ошибкой: %s — fallback на HTML", err)
                        api_failed = True
                        break

                if not api_failed and (summaries or bans or games):
                    used_api = True
                    _fill_from_api(result, summaries, bans, games)

            except SteamApiError as e:
                log.info("Steam API ошибка %s — переключаемся на HTML", e.code)
            except Exception as e:
                log.warning("Steam API general error: %s — переключаемся на HTML", e)

        # Если API не сработал — парсим HTML напрямую
        if not used_api and sid is not None:
            if scraped_profile is None:
                log.info("Парсим профиль %s через HTML (fallback)", sid)
                scraped = await _scrape_profile(session, sid)
                if scraped:
                    _fill_from_scrape(result, scraped)
                    result["source"] = "html"
                else:
                    # Не смогли даже HTML получить
                    if result.get("persona") is None:
                        result["error"] = ("Не удалось получить данные профиля. "
                                          "Steam может быть недоступен.")
                        return result
            else:
                # Уже есть scraped данные
                result["source"] = "html"

            # Пробуем получить часы в Rust через HTML /games/
            if result["profile_state"] == "public" and result["hours_rust"] is None:
                log.info("Парсим часы в Rust через HTML /games/")
                rust_hours = await _scrape_rust_hours(session, sid)
                if rust_hours is not None:
                    result["hours_rust"] = rust_hours

        elif used_api:
            result["source"] = "api" if result["source"] == "unknown" else "mixed"

            # Если API не дал часы (приватный) — пробуем HTML
            if result["hours_rust"] is None and result["profile_state"] == "public":
                log.info("API не дал часы в Rust — пробуем HTML")
                rust_hours = await _scrape_rust_hours(session, sid)
                if rust_hours is not None:
                    result["hours_rust"] = rust_hours
                    result["source"] = "mixed"

        # ── Финализация ────────────────────────────────────────────────────────
        result["last_seen"] = _format_last_seen(result["last_logoff"])
        result["account_created"] = _format_unix(result["time_created"])

        result["success"] = True

        # Сохраняем в кэш
        _cache_set(sid, result.copy())

        return result


def _fill_from_api(result: dict, summaries: dict, bans: dict, games: dict) -> None:
    """Заполняет result данными из Steam Web API."""
    if summaries:
        result["persona"] = summaries.get("personaname") or result["persona"]
        result["avatar"] = summaries.get("avatarfull") or result["avatar"]
        vis = summaries.get("communityvisibilitystate", 0)
        result["profile_state"] = "private" if vis in (1, 2) else "public"
        result["last_logoff"] = summaries.get("lastlogoff")
        result["time_created"] = summaries.get("timecreated")
        result["country_code"] = summaries.get("loccountrycode")
        result["currently_playing"] = summaries.get("gameextrainfo")

    if bans:
        result["vac_banned"] = bool(bans.get("VACBanned", False))
        result["community_banned"] = bool(bans.get("CommunityBanned", False))
        result["days_since_last_ban"] = bans.get("DaysSinceLastBan")

    # Часы в Rust
    rust = games.get(RUST_APP_ID)
    if rust is not None:
        # playtime_forever в минутах
        result["hours_rust"] = round(rust.get("playtime_forever", 0) / 60, 1)


def _fill_from_scrape(result: dict, scraped: dict) -> None:
    """Заполняет result данными из HTML-скрейпинга."""
    if not scraped:
        return
    if scraped.get("persona"):
        result["persona"] = scraped["persona"]
    if scraped.get("avatar"):
        result["avatar"] = scraped["avatar"]
    if scraped.get("profile_state") and scraped["profile_state"] != "unknown":
        result["profile_state"] = scraped["profile_state"]
    if scraped.get("online_status") and scraped["online_status"] != "unknown":
        result["online_status"] = scraped["online_status"]
    if scraped.get("last_logoff"):
        result["last_logoff"] = scraped["last_logoff"]
    if scraped.get("time_created"):
        result["time_created"] = scraped["time_created"]
    if scraped.get("country_code"):
        result["country_code"] = scraped["country_code"]
    if scraped.get("currently_playing"):
        result["currently_playing"] = scraped["currently_playing"]
    if scraped.get("steamid") and not result.get("steamid"):
        result["steamid"] = scraped["steamid"]
    if scraped.get("profile_url") and not result.get("profile_url"):
        result["profile_url"] = scraped["profile_url"]
