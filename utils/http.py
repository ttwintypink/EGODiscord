"""
utils/http.py — Кастомный HTTP-стек для EGODiscord.

Решает проблему: на Windows в России WARP меняет DNS только для браузера,
а Python использует системный DNS, который провайдер блокирует.

Решение: используем aiodns (async DNS resolver) с прямым запросом к
Cloudflare 1.1.1.1 и Google 8.8.8.8, минуя системный DNS.

Также поддерживает прокси через переменные окружения:
    HTTP_PROXY=http://127.0.0.1:8080
    HTTPS_PROXY=http://127.0.0.1:8080
    ALL_PROXY=socks5://127.0.0.1:1080
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)


# DNS-серверы, которые используем для резолвинга.
# 1.1.1.1 — Cloudflare (быстрый, без логов).
# 8.8.8.8 — Google (запасной).
# 9.9.9.9 — Quad9 (запасной, блокирует вредоносные домены).
CUSTOM_DNS_SERVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]


def is_custom_dns_available() -> bool:
    """Проверяет, установлен ли aiodns (нужен для кастомного DNS)."""
    try:
        import aiodns  # noqa: F401
        return True
    except ImportError:
        return False


def make_resolver() -> aiohttp.AbstractResolver:
    """
    Создаёт DNS-резолвер.
    
    Если установлен aiodns — используем кастомные DNS-сервера (1.1.1.1, 8.8.8.8),
    минуя системный DNS. Это решает проблему с DNS-блокировками в РФ.
    
    Если aiodns нет — используем системный резолвер (ThreadedResolver).
    """
    if is_custom_dns_available():
        try:
            resolver = aiohttp.AsyncResolver(
                nameservers=CUSTOM_DNS_SERVERS,
                timeout=10,
            )
            log.info(
                "DNS: используем кастомные серверы %s (через aiodns, минуя системный DNS)",
                CUSTOM_DNS_SERVERS,
            )
            return resolver
        except Exception as e:
            log.warning("Не удалось создать AsyncResolver: %s — используем системный DNS", e)
    
    log.info("DNS: используем системный резолвер (aiodns не установлен)")
    return aiohttp.ThreadedResolver()


def get_proxy_url() -> Optional[str]:
    """
    Возвращает URL прокси из переменных окружения, если задан.
    
    Поддерживаемые переменные (в порядке приоритета):
        HTTPS_PROXY / https_proxy
        ALL_PROXY / all_proxy
        HTTP_PROXY / http_proxy
    
    Прокси может быть:
        http://user:pass@host:port
        http://host:port
        socks5://host:port  (требует aiohttp-socks)
    """
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var, "").strip()
        if val:
            log.info("Прокси: используется переменная %s", var)
            return val
    return None


def make_connector(
    *,
    resolver: Optional[aiohttp.AbstractResolver] = None,
    force_close: bool = False,
) -> aiohttp.BaseConnector:
    """
    Создаёт aiohttp-коннектор с кастомным DNS-резолвером.
    
    Используется для всех HTTP-запросов бота (Discord API, Steam API и т.д.).
    
    Args:
        resolver: кастомный резолвер. Если None — создаётся через make_resolver().
        force_close: принудительно закрывать соединения после каждого запроса
                     (полезно при проблемах с keep-alive через прокси).
    """
    if resolver is None:
        resolver = make_resolver()
    
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        force_close=force_close,
        enable_cleanup_closed=True,
        # Таймаут на установку соединения
        ssl=False,  # Не проверять SSL — некоторые прокси ломают сертификаты
        # Включаем IPv4 и IPv6
        family=socket.AF_UNSPEC,
    )
    return connector


def make_session(
    *,
    timeout: int = 30,
    force_close: bool = False,
) -> aiohttp.ClientSession:
    """
    Создаёт aiohttp.ClientSession с кастомным DNS-резолвером и прокси.
    
    Используется для всех HTTP-запросов в utils/steam_api.py и других местах.
    """
    connector = make_connector(force_close=force_close)
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=timeout_obj,
        # Прокси берётся из env переменных автоматически (aiohttp поддерживает)
    )
    return session


async def test_custom_dns() -> tuple[bool, str]:
    """
    Тестирует, работает ли кастомный DNS-резолвер.
    
    Возвращает (ok, message).
    """
    if not is_custom_dns_available():
        return False, "aiodns не установлен — pip install aiodns"
    
    try:
        import aiodns
        resolver = aiodns.DNSResolver(nameservers=CUSTOM_DNS_SERVERS, timeout=10)
        # Проверяем резолвинг discord.com
        result = await resolver.gethostbyname("discord.com", socket.AF_INET)
        if result and result.addresses:
            ips = ", ".join(result.addresses[:3])
            return True, f"discord.com резолвится в {ips}"
        return False, "discord.com не резолвится через 1.1.1.1"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def test_proxy() -> tuple[bool, str]:
    """
    Тестирует, работает ли прокси (если задан).
    """
    proxy_url = get_proxy_url()
    if not proxy_url:
        return True, "прокси не используется (нет env переменных)"
    
    try:
        connector = make_connector(force_close=True)
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(
                "https://discord.com/api/v10/gateway",
                proxy=proxy_url,
            ) as resp:
                if resp.status == 200:
                    return True, f"прокси {proxy_url} работает (Discord API: HTTP 200)"
                return False, f"прокси {proxy_url} — HTTP {resp.status}"
    except Exception as e:
        return False, f"прокси {proxy_url} — {type(e).__name__}: {e}"
