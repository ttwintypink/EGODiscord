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

import asyncio
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


def _has_running_loop() -> bool:
    """Проверяет, есть ли active event loop (не падает, как get_running_loop)."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def make_resolver() -> Optional[aiohttp.AbstractResolver]:
    """
    Создаёт DNS-резолвер.
    
    Если установлен aiodns — используем кастомные DNS-сервера (1.1.1.1, 8.8.8.8),
    минуя системный DNS. Это решает проблему с DNS-блокировками в РФ.
    
    Если aiodns нет — используем системный резолвер (ThreadedResolver).
    
    ВАЖНО: в aiohttp 3.14+ ThreadedResolver и AsyncResolver требуют running event loop
    для создания. Если функция вызвана вне event loop (на top-level скрипта) —
    возвращает None, и тогда make_connector() не передаёт resolver в TCPConnector
    (используется default).
    """
    # aiohttp 3.14+ требует running event loop для создания resolver'а.
    # На top-level скрипта loop ещё не запущен — возвращаем None.
    if not _has_running_loop():
        log.info("DNS: нет running event loop — resolver будет создан позже (default)")
        return None
    
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
    try:
        return aiohttp.ThreadedResolver()
    except RuntimeError:
        # На всякий случай — если loop исчез между проверкой и вызовом
        log.warning("ThreadedResolver не создан (нет event loop) — используем default")
        return None


def get_proxy_url() -> Optional[str]:
    """
    Возвращает URL прокси.
    
    Приоритет:
        1. Переменные окружения HTTPS_PROXY / https_proxy / ALL_PROXY / all_proxy
           / HTTP_PROXY / http_proxy
        2. config.json -> "proxy" (если задан)
    
    Прокси может быть:
        http://user:pass@host:port
        http://host:port
        socks5://user:pass@host:port  (требует aiohttp-socks)
        socks5://host:port
        socks4://host:port
    """
    # 1. env переменные (приоритет выше)
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var, "").strip()
        if val:
            log.info("Прокси: используется переменная окружения %s", var)
            return val
    
    # 2. config.json -> "proxy"
    try:
        import json
        from pathlib import Path
        cfg_path = Path("config.json")
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg_proxy = (cfg.get("proxy") or "").strip()
            if cfg_proxy and cfg_proxy.lower() not in ("none", "null", "false", ""):
                log.info("Прокси: используется config.json -> proxy = %s", _mask_proxy(cfg_proxy))
                return cfg_proxy
    except Exception as e:
        log.warning("Не удалось прочитать proxy из config.json: %s", e)
    
    return None


def _mask_proxy(proxy_url: str) -> str:
    """Маскирует пароль в URL прокси для логов."""
    if "@" in proxy_url and "://" in proxy_url:
        # http://user:pass@host:port → http://user:***@host:port
        scheme, rest = proxy_url.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{scheme}://{user}:***@{host}"
            return f"{scheme}://***@{host}"
    return proxy_url


def is_socks_proxy(proxy_url: str) -> bool:
    """Проверяет, является ли прокси SOCKS-прокси (нужен aiohttp-socks)."""
    if not proxy_url:
        return False
    return proxy_url.lower().startswith(("socks5://", "socks5h://", "socks4://", "socks4a://"))


def check_socks_support() -> tuple[bool, str]:
    """
    Проверяет, доступна ли библиотека aiohttp-socks (для SOCKS5/4 прокси).
    Возвращает (ok, message).
    """
    try:
        import aiohttp_socks  # noqa: F401
        return True, "aiohttp-socks установлен"
    except ImportError:
        return False, "aiohttp-socks не установлен — pip install aiohttp-socks"


def make_connector(
    *,
    resolver: Optional[aiohttp.AbstractResolver] = None,
    force_close: bool = False,
) -> Optional[aiohttp.BaseConnector]:
    """
    Создаёт aiohttp-коннектор с кастомным DNS-резолвером.
    
    Используется для всех HTTP-запросов бота (Discord API, Steam API и т.д.).
    
    Если в config.json или env задан SOCKS5/4 прокси — автоматически
    используется ProxyConnector из aiohttp-socks (оборачивает TCPConnector).
    
    Args:
        resolver: кастомный резолвер. Если None — создаётся через make_resolver().
        force_close: принудительно закрывать соединения после каждого запроса
                     (полезно при проблемах с keep-alive через прокси).
    
    Returns:
        Connector или None, если нет running event loop (aiohttp 3.14+ требует
        loop для создания TCPConnector). В этом случае вызывающий код должен
        использовать default connector (передать None в Bot или дождаться async context).
    """
    # aiohttp 3.14+ требует running event loop для TCPConnector.__init__.
    # Если нет loop — возвращаем None, вызывающий код использует default.
    if not _has_running_loop():
        log.info("Connector: нет running event loop — возвращаем None (будет использован default)")
        return None
    
    if resolver is None:
        resolver = make_resolver()
    
    # Если resolver=None (нет loop или aiodns не установлен) — не передаём,
    # TCPConnector создаст default ThreadedResolver сам.
    kwargs = {
        "force_close": force_close,
        "enable_cleanup_closed": True,
        "ssl": False,  # Не проверять SSL — некоторые прокси ломают сертификаты
        "family": socket.AF_UNSPEC,
    }
    if resolver is not None:
        kwargs["resolver"] = resolver
    
    connector = aiohttp.TCPConnector(**kwargs)

    # Если задан SOCKS-прокси — оборачиваем в ProxyConnector.
    # Это позволяет использовать socks5://... прокси напрямую.
    proxy_url = get_proxy_url()
    if proxy_url and is_socks_proxy(proxy_url):
        socks_ok, socks_msg = check_socks_support()
        if socks_ok:
            try:
                from aiohttp_socks import ProxyConnector
                # ProxyConnector создаёт свой внутренний коннектор,
                # поэтому наш TCPConnector больше не нужен.
                # make_connector — sync функция, поэтому не можем await
                # connector.close(). Вместо этого планируем закрытие через
                # loop callback —connector._close() это синхронная часть close.
                socks_connector = ProxyConnector.from_url(proxy_url, **kwargs)
                log.info("Connector: используется SOCKS-прокси через aiohttp-socks: %s",
                         _mask_proxy(proxy_url))
                try:
                    # Создаём task на закрытие в event loop, если он запущен
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(connector.close())
                        else:
                            # loop не запущен — закрываем синхронно (небезопасно, но лучше чем утечка)
                            loop.run_until_complete(connector.close())
                    except RuntimeError:
                        # нет event loop — забиваем, утечка минимальна (создаётся 1 раз при старте)
                        pass
                except Exception:
                    pass
                return socks_connector
            except Exception as e:
                log.warning("Не удалось создать SOCKS ProxyConnector: %s — используем HTTP-прокси через env", e)
        else:
            log.warning("SOCKS-прокси задан, но aiohttp-socks не установлен: %s", socks_msg)
            log.warning("Установите: pip install aiohttp-socks")

    return connector


def make_session(
    *,
    timeout: int = 30,
    force_close: bool = False,
) -> aiohttp.ClientSession:
    """
    Создаёт aiohttp.ClientSession с кастомным DNS-резолвером и прокси.
    
    Используется для всех HTTP-запросов в utils/steam_api.py и других местах.
    
    ВАЖНО: должна вызываться внутри async context (где есть running event loop).
    Если вызвана вне loop — aiohttp.ClientSession создаст default connector.
    """
    connector = make_connector(force_close=force_close)
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    # Если connector=None (нет loop) — aiohttp создаст default при первом запросе
    kwargs = {"timeout": timeout_obj}
    if connector is not None:
        kwargs["connector"] = connector
    return aiohttp.ClientSession(**kwargs)


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
        session_kwargs = {"timeout": timeout}
        if connector is not None:
            session_kwargs["connector"] = connector
        async with aiohttp.ClientSession(**session_kwargs) as session:
            async with session.get(
                "https://discord.com/api/v10/gateway",
                proxy=proxy_url,
            ) as resp:
                if resp.status == 200:
                    return True, f"прокси {proxy_url} работает (Discord API: HTTP 200)"
                return False, f"прокси {proxy_url} — HTTP {resp.status}"
    except Exception as e:
        return False, f"прокси {proxy_url} — {type(e).__name__}: {e}"
