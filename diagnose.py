"""
diagnose.py — Диагностика сети и конфигурации бота.

Запуск:
    python diagnose.py

Проверяет:
1. Доступность Discord API
2. Доступность Discord Gateway (WebSocket)
3. Доступность Steam API
4. Доступность Steam Community
5. Корректность config.json
6. Корректность .env (если есть)
7. Наличие всех зависимостей
8. Доступность Discord CDN

В конце даёт конкретные рекомендации в зависимости от того, что не работает.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse

# Загружаем .env ЧЕРЕЗ python-dotenv — иначе DISCORD_TOKEN не виден в diagnose.py
# (bot.py тоже загружает .env через load_dotenv, здесь делаем так же)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv не установлен — переменные только из системы

# Цветной вывод (работает на Windows 10+)
if platform.system() == "Windows":
    os.system("")  # активируем ANSI-обработку в cmd.exe

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str):
    print(f"  {GREEN}✅{RESET} {msg}")


def fail(msg: str):
    print(f"  {RED}❌{RESET} {msg}")


def warn(msg: str):
    print(f"  {YELLOW}⚠️{RESET} {msg}")


def info(msg: str):
    print(f"  {CYAN}ℹ️{RESET} {msg}")


def header(msg: str):
    print(f"\n{BOLD}{CYAN}═══ {msg} ═══{RESET}")


# ============================================================================
# Проверки
# ============================================================================

async def check_url(name: str, url: str, expected_status: int = 200,
                    timeout: int = 15) -> tuple[bool, str]:
    """Проверяет URL, возвращает (ok, detail)."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == expected_status:
                    return True, f"HTTP {resp.status}"
                return False, f"HTTP {resp.status} (ожидался {expected_status})"
    except asyncio.TimeoutError:
        return False, f"таймаут {timeout}с"
    except aiohttp.ClientConnectorError as e:
        return False, f"connection error: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def check_tcp(host: str, port: int, timeout: int = 10) -> tuple[bool, str]:
    """Проверяет TCP-соединение с host:port."""
    try:
        future = asyncio.open_connection(host, port, ssl=None)
        reader, writer = await asyncio.wait_for(future, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, "подключение установлено"
    except asyncio.TimeoutError:
        return False, f"таймаут {timeout}с"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def check_dns(host: str) -> tuple[bool, str]:
    """Проверяет DNS-резолвинг."""
    try:
        loop = asyncio.get_event_loop()
        addrs = await loop.getaddrinfo(host, None)
        ip = addrs[0][4][0] if addrs else "?"
        return True, f"разрешён в {ip}"
    except socket.gaierror as e:
        return False, f"DNS error: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_python():
    """Проверка версии Python и платформы."""
    header("1. Python и платформа")
    info(f"Python: {sys.version.split()[0]}")
    info(f"Платформа: {platform.system()} {platform.release()} ({platform.machine()})")
    info(f"Архитектура: {'64-bit' if sys.maxsize > 2**32 else '32-bit'}")
    
    if sys.version_info < (3, 10):
        fail("Python < 3.10 — нужен 3.10 или выше")
    else:
        ok(f"Python {sys.version_info.major}.{sys.version_info.minor} подходит")
    
    if platform.system() == "Windows":
        if sys.maxsize <= 2**32:
            fail("32-битный Python на Windows — могут быть проблемы с aiohttp")
            warn("Рекомендуется установить 64-битный Python")
        else:
            ok("64-битный Python")


def check_dependencies():
    """Проверка зависимостей."""
    header("2. Зависимости Python")
    
    deps = [
        ("discord", "discord.py"),
        ("aiohttp", "aiohttp"),
        ("aiosqlite", "aiosqlite"),
        ("dotenv", "python-dotenv"),
        ("aiodns", "aiodns  ⚠️ КРИТИЧНО для РФ — без него DNS не обойдёт блокировку"),
        ("aiohttp_socks", "aiohttp-socks  (нужен только для SOCKS5 прокси)"),
    ]
    
    for mod_name, pip_name in deps:
        try:
            mod = __import__(mod_name)
            version = getattr(mod, "__version__", "неизвестно")
            ok(f"{pip_name} {version}")
        except ImportError:
            if "aiodns" in pip_name:
                fail(f"{pip_name} не установлен — запустите: pip install {mod_name}")
                warn("Без aiodns бот не сможет обойти DNS-блокировки в РФ")
            elif "aiohttp_socks" in pip_name:
                info(f"{pip_name} не установлен (нужен только для SOCKS5 прокси)")
                info("  Установить: pip install aiohttp-socks")
            else:
                fail(f"{pip_name} не установлен — запустите: pip install {mod_name}")
    
    # Опциональная: bs4
    try:
        import bs4
        ok(f"beautifulsoup4 {bs4.__version__} (опционально, не требуется)")
    except ImportError:
        info("beautifulsoup4 не установлен (это нормально, бот работает без него)")


def check_config():
    """Проверка config.json."""
    header("3. Конфигурация config.json")
    
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        fail("config.json не найден!")
        return None
    
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        fail(f"config.json повреждён: {e}")
        return None
    except Exception as e:
        fail(f"Не удалось прочитать config.json: {e}")
        return None
    
    ok("config.json читается")
    
    # Обязательные поля
    required_fields = {
        "steam_api_key": "Steam API ключ",
        "developer_id": "ID разработчика",
        "category_clan_id": "ID категории клана",
        "category_mod_id": "ID категории модерации",
        "log_channel_id": "ID канала логов",
        "accept_role_id": "ID роли при принятии",
    }
    
    for field, desc in required_fields.items():
        val = cfg.get(field)
        if not val or val == 0:
            warn(f"{desc} ({field}) не задан — бот может работать некорректно")
        else:
            ok(f"{desc}: {field}={val}")
    
    # Проверка ключа Steam
    steam_key = cfg.get("steam_api_key", "")
    if steam_key and len(steam_key) >= 16:
        masked = steam_key[:6] + "…" + steam_key[-4:]
        ok(f"Steam API ключ задан: {masked}")
    else:
        fail("Steam API ключ не задан или слишком короткий")
    
    # Проверка вопросов
    q_clan = cfg.get("questions_clan", [])
    q_mod = cfg.get("questions_mod", [])
    ok(f"Вопросов анкеты — клан: {len(q_clan)}, модерация: {len(q_mod)}")
    
    return cfg


def check_env():
    """Проверка .env."""
    header("4. Переменные окружения (.env)")
    
    env_path = Path(".env")
    if not env_path.exists():
        info(".env не найден — переменные берутся из системы")
    else:
        ok(".env найден")
    
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if token:
        masked = token[:8] + "…" + token[-4:] if len(token) > 12 else "(короткий?)"
        ok(f"DISCORD_TOKEN задан: {masked}")
    else:
        fail("DISCORD_TOKEN не задан в переменных окружения")
    
    steam = os.environ.get("STEAM_API_KEY", "").strip()
    if steam:
        ok(f"STEAM_API_KEY задан (из env, перекрывает config.json)")
    
    dev_id = os.environ.get("DEVELOPER_ID", "").strip()
    if dev_id:
        ok(f"DEVELOPER_ID задан: {dev_id}")


async def check_network():
    """Проверка сети."""
    header("5. Доступность Discord")
    
    # 5a. Системный DNS (как делает браузер)
    print("\n  Системный DNS (как браузер):")
    dns_ok, dns_detail = await check_dns("discord.com")
    if dns_ok:
        ok(f"discord.com {dns_detail}")
    else:
        fail(f"discord.com: {dns_detail}")
        warn("Системный DNS не работает — это типично для РФ")
    
    # 5b. Кастомный DNS через 1.1.1.1 / 8.8.8.8
    print("\n  Кастомный DNS (1.1.1.1 / 8.8.8.8 через aiodns):")
    try:
        from utils.http import test_custom_dns, is_custom_dns_available
        if not is_custom_dns_available():
            fail("aiodns не установлен — бот не сможет обойти DNS-блокировку")
            warn("Установите: pip install aiodns")
            custom_dns_ok = False
        else:
            custom_dns_ok, custom_detail = await test_custom_dns()
            if custom_dns_ok:
                ok(f"Кастомный DNS работает: {custom_detail}")
            else:
                fail(f"Кастомный DNS не работает: {custom_detail}")
                warn("Возможно, провайдер блокирует и 1.1.1.1 — нужен VPN с туннелем")
    except ImportError:
        fail("utils.http не найден — проверьте структуру проекта")
        custom_dns_ok = False
    
    # 5c. TCP-соединение через кастомный DNS
    print("\n  TCP-соединение (через кастомный DNS):")
    try:
        from utils.http import make_session
        import aiohttp
        async with make_session(timeout=10) as session:
            try:
                async with session.get(
                    "https://discord.com/api/v10/gateway"
                ) as resp:
                    if resp.status == 200:
                        ok(f"Discord API: HTTP {resp.status} — доступен")
                        tcp_ok = True
                    else:
                        fail(f"Discord API: HTTP {resp.status}")
                        tcp_ok = False
            except Exception as e:
                fail(f"TCP-соединение: {type(e).__name__}: {e}")
                tcp_ok = False
    except ImportError:
        fail("utils.http не найден")
        tcp_ok = False
    
    # 5d. HTTP API через системный DNS
    print("\n  HTTP API через системный DNS:")
    api_ok, api_detail = await check_url(
        "Discord API", "https://discord.com/api/v10/gateway"
    )
    if api_ok:
        ok(f"Discord API (system DNS): {api_detail}")
    else:
        fail(f"Discord API (system DNS): {api_detail}")
    
    # Итог
    return tcp_ok or (custom_dns_ok and api_ok)


async def check_proxy():
    """Проверка прокси."""
    header("6. Прокси (если задан)")
    
    try:
        from utils.http import (test_proxy, get_proxy_url, is_socks_proxy,
                                  check_socks_support, _mask_proxy)
        proxy_url = get_proxy_url()
        if not proxy_url:
            info("Прокси не задан")
            info("Способы задать прокси:")
            info("  Способ 1: config.json → поле \"proxy\"")
            info("    \"proxy\": \"socks5://user:pass@host:port\"")
            info("    \"proxy\": \"http://host:port\"")
            info("  Способ 2: переменные окружения")
            info("    set HTTPS_PROXY=http://127.0.0.1:8080")
            info("    set HTTP_PROXY=http://127.0.0.1:8080")
            info("    python bot.py")
            info("  Способ 3: ALL_PROXY для SOCKS5")
            info("    set ALL_PROXY=socks5://127.0.0.1:1080")
            info("    (требуется: pip install aiohttp-socks)")
            return True
        
        ok(f"Прокси задан: {_mask_proxy(proxy_url)}")
        
        # Если SOCKS — проверяем что есть aiohttp-socks
        if is_socks_proxy(proxy_url):
            socks_ok, socks_msg = check_socks_support()
            if socks_ok:
                ok(f"SOCKS-поддержка: {socks_msg}")
            else:
                fail(f"SOCKS-поддержка: {socks_msg}")
                warn("Установите: pip install aiohttp-socks")
                return False
        
        proxy_ok, proxy_detail = await test_proxy()
        if proxy_ok:
            ok(f"Прокси работает: {proxy_detail}")
        else:
            fail(f"Прокси не работает: {proxy_detail}")
        return proxy_ok
    except ImportError:
        fail("utils.http не найден")
        return False


async def check_steam():
    """Проверка Steam API."""
    header("7. Доступность Steam")
    
    steam_ok, steam_detail = await check_url(
        "Steam API", "https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/"
    )
    if steam_ok:
        ok(f"Steam API: {steam_detail}")
    else:
        fail(f"Steam API: {steam_detail}")
    
    comm_ok, comm_detail = await check_url(
        "Steam Community", "https://steamcommunity.com/"
    )
    if comm_ok:
        ok(f"Steam Community: {comm_detail}")
    else:
        warn(f"Steam Community: {comm_detail} (не критично для работы API)")
    
    return steam_ok


async def check_steam_api_key(api_key: str):
    """Проверка валидности Steam API ключа."""
    header("8. Тест Steam API ключа")
    
    if not api_key or len(api_key) < 16:
        fail("Ключ пустой или слишком короткий")
        return False
    
    import aiohttp
    url = f"https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/?key={api_key}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    ok(f"Ключ валиден (HTTP 200)")
                    return True
                elif resp.status == 403:
                    fail(f"Ключ невалиден (HTTP 403 — Forbidden)")
                    warn("Получить новый ключ: https://steamcommunity.com/dev/apikey")
                    return False
                else:
                    warn(f"Неожиданный статус: HTTP {resp.status}")
                    return False
    except Exception as e:
        fail(f"Ошибка проверки: {type(e).__name__}: {e}")
        return False


# ============================================================================
# Главная функция
# ============================================================================

async def main():
    print(f"{BOLD}{CYAN}")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║      EGODiscord System — диагностика                     ║")
    print("║      Проверка сети, конфига и зависимостей               ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(RESET)
    
    # 1. Python
    check_python()
    
    # 2. Зависимости
    check_dependencies()
    
    # 3. Конфиг
    cfg = check_config()
    
    # 4. .env
    check_env()
    
    # 5. Discord сеть
    discord_ok = await check_network()
    
    # 6. Прокси (если задан)
    proxy_ok = await check_proxy()
    
    # 7. Steam сеть
    steam_net_ok = await check_steam()
    
    # 8. Steam API ключ
    if cfg:
        api_key = cfg.get("steam_api_key", "")
        # env перекрывает
        api_key = os.environ.get("STEAM_API_KEY", api_key).strip()
        if api_key and steam_net_ok:
            await check_steam_api_key(api_key)
    
    # Итоговая диагностика
    header("ИТОГОВАЯ ДИАГНОСТИКА")
    
    if discord_ok:
        ok("Discord доступен — бот должен запускаться")
    else:
        print()
        print(f"  {RED}{BOLD}Discord НЕДОСТУПЕН — бот не запустится{RESET}")
        print()
        print(f"  {BOLD}Что делать:{RESET}")
        print()
        print(f"  {CYAN}1. Cloudflare WARP в режиме WARP (не 1.1.1.1 only):{RESET}")
        print(f"     • Открой WARP → Settings → Advanced → Connection options")
        print(f"     • Выбери режим WARP (не 1.1.1.1 only)")
        print(f"     • Перезапусти WARP и попробуй снова")
        print()
        print(f"  {CYAN}2. Системный VPN (Outline / Amnezia / Wireguard):{RESET}")
        print(f"     • Установи VPN-клиент с туннелированием трафика")
        print(f"     • Включи VPN — весь трафик пойдёт через него")
        print(f"     • Браузер и Python будут работать одинаково")
        print()
        print(f"  {CYAN}3. HTTP/SOCKS5 прокси через config.json (САМЫЙ ПРОСТОЙ ВАРИАНТ):{RESET}")
        print(f"     • Открой config.json в блокноте")
        print(f"     • Добавь поле \"proxy\":")
        print(f'         "proxy": "http://host:port"')
        print(f'         "proxy": "socks5://user:pass@host:port"')
        print(f"     • Для SOCKS5 установи: pip install aiohttp-socks")
        print(f"     • Бот автоматически подхватит прокси")
        print()
        print(f"  {CYAN}4. Прокси через переменные окружения:{RESET}")
        print(f"     • set HTTPS_PROXY=http://127.0.0.1:8080")
        print(f"     • set HTTP_PROXY=http://127.0.0.1:8080")
        print(f"     • python bot.py")
        print()
        print(f"  {CYAN}5. Проверь антивирус:{RESET}")
        print(f"     • Добавь python.exe в исключения Windows Defender")
        print(f"     • Отключи временно Firewall")
        print()
        print(f"  После настройки сети запусти 'python diagnose.py' снова.")
    
    print()
    if discord_ok and (not cfg or not cfg.get("steam_api_key")):
        warn("Discord OK, но Steam API ключ не задан — Steam-проверки не будут работать")
    elif discord_ok and cfg and cfg.get("steam_api_key"):
        ok("Все системы готовы к запуску бота!")
    
    print()
    print(f"{BOLD}Конец диагностики.{RESET}")
    
    return 0 if discord_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nПрервано пользователем")
        sys.exit(130)
