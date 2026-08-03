"""
bot.py — Точка входа EGODiscord.

Загружает конфиг, инициализирует БД, регистрирует persistent Views
(чтобы кнопки работали после перезапуска), загружает все коги.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands

# Опционально поддерживаем python-dotenv для локальной разработки.
# В проде (на хостинге) переменные обычно задаются через панель хостинга.
try:
    from dotenv import load_dotenv
    load_dotenv()
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False

import database
from cogs.ticket_control import (
    TicketControlView,
    TicketControlViewClaimed,
    RestoreTicketView,
)
from cogs.tickets import TicketPanelView, ApplicationModal

# --- Логирование ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("EGODiscord")

# Уровень шума от discord.py
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)


# --- Загрузка конфига -------------------------------------------------------
def load_config() -> dict:
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        log.error("config.json не найден! Создайте его по образцу из README.")
        sys.exit(1)
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Переопределения из переменных окружения (для прод-хостинга).
    # Переменная окружения имеет приоритет над config.json — это позволяет
    # хранить секреты вне репозитория.
    env_steam_key = os.environ.get("STEAM_API_KEY", "").strip()
    if env_steam_key:
        cfg["steam_api_key"] = env_steam_key
        log.info("Steam API ключ загружен из переменной окружения STEAM_API_KEY.")

    env_dev_id = os.environ.get("DEVELOPER_ID", "").strip()
    if env_dev_id.isdigit():
        cfg["developer_id"] = int(env_dev_id)

    return cfg


CONFIG = load_config()

# Подготовка директорий
os.makedirs("transcripts", exist_ok=True)
os.makedirs("logs", exist_ok=True)


# --- Bot instance -----------------------------------------------------------
intents = discord.Intents.all()

# Создаём кастомный HTTP-коннектор с DNS-резолвером 1.1.1.1 / 8.8.8.8.
# Это решает проблему: в РФ WARP меняет DNS только для браузера, а Python
# использует системный DNS, который провайдер блокирует.
# С кастомным резолвером бот сам обращается к Cloudflare DNS напрямую.
from utils.http import make_connector, is_custom_dns_available, get_proxy_url

_proxy_url = get_proxy_url()
_bot_connector = make_connector(force_close=False)
log.info(
    "HTTP-коннектор создан. Кастомный DNS: %s. Прокси: %s",
    "включён (1.1.1.1, 8.8.8.8)" if is_custom_dns_available() else "недоступен (aiodns не установлен)",
    _proxy_url or "не используется",
)

# Передаём connector и proxy в Bot — discord.Client сам создаст HTTPClient.
# Если прокси задан (HTTPS_PROXY env var), он будет использоваться для всех
# запросов к Discord API.
bot = commands.Bot(
    command_prefix=".",
    intents=intents,
    help_command=None,
    case_insensitive=True,
    connector=_bot_connector,
    proxy=_proxy_url,
)


@bot.event
async def on_ready():
    log.info("═══════════════════════════════════════════════════════════")
    log.info("  EGODiscord System — запущен")
    log.info("  Пользователь: %s (ID: %s)", bot.user, bot.user.id)
    log.info("  Серверов: %d", len(bot.guilds))
    log.info("  Префикс команд: '.'")
    log.info("═══════════════════════════════════════════════════════════")

    # Синхронизируем persistent views (кнопки, которые должны «жить» между
    # перезапусками бота).
    # - TicketPanelView, TicketControlView, RestoreTicketView — регистрируем как persistent.
    # - RatingView — НЕ регистрируем (динамический custom_id вида ego_rate_<stars>_<recruiter_id>).
    #   Обработка идёт через on_interaction listener в TicketControl cog.
    # - CloseDecisionView, ConfirmCloseView — временные (timeout), не persistent.
    bot.add_view(TicketPanelView(CONFIG))
    bot.add_view(TicketControlView(CONFIG))
    bot.add_view(TicketControlViewClaimed(CONFIG))
    bot.add_view(RestoreTicketView(CONFIG))

    # Запускаем фоновые задачи (если ещё не запущены)
    ticket_control_cog = bot.get_cog("TicketControl")
    if ticket_control_cog:
        ticket_control_cog.start_background_tasks()

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="за заявками EGO | .help"
        ),
    )


# --- Глобальная обработка ошибок команд ------------------------------------
@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    """Ловим ошибки команд — пишем пользователю, в лог без шума."""
    # CommandNotFound — пользователь написал несуществующую команду.
    # Не логируем как ERROR (мусор), просто игнорируем.
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        try:
            embed = discord.Embed(
                title="⚠️ Недостаточно аргументов",
                description=f"Команда `.{ctx.command}` требует аргументы.\n"
                            f"Использование: `.{ctx.command} {ctx.command.signature}`",
                color=0xFEE75C,
            )
            embed.set_footer(text="EGODiscord System")
            await ctx.send(embed=embed)
        except discord.HTTPException:
            pass
        return

    if isinstance(error, commands.CheckFailure):
        # Молча игнорируем проверки (например, guild_only)
        return

    # Прочие ошибки — логируем
    log.exception("Ошибка в команде %s: %s", ctx.command, error)
    try:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=f"Произошла ошибка при выполнении команды.\n"
                        f"```\n{type(error).__name__}: {error}\n```",
            color=0xED4245,
        )
        embed.set_footer(text="EGODiscord System")
        await ctx.send(embed=embed)
    except discord.HTTPException:
        pass


# --- Загрузка когов ---------------------------------------------------------
INITIAL_COGS = [
    "cogs.tickets",
    "cogs.ticket_control",
    "cogs.moderation",
    "cogs.developer",
    "cogs.editor",
    "cogs.help",
]


def _resolve_token() -> str:
    """
    Возвращает токен бота.

    Приоритет:
        1. Переменная окружения DISCORD_TOKEN
           (рекомендуется — задаётся через панель хостинга или .env файл).
        2. config.json -> token (fallback, не рекомендуется для публичных репозиториев).
    """
    env_token = os.environ.get("DISCORD_TOKEN", "").strip()
    if env_token:
        log.info("Токен загружен из переменной окружения DISCORD_TOKEN.")
        return env_token

    cfg_token = (CONFIG.get("token") or "").strip()
    if cfg_token and cfg_token != "ВСТАВЬ_ТОКЕН_БОТА":
        log.warning("Токен загружен из config.json. Рекомендуется использовать "
                    "переменную окружения DISCORD_TOKEN.")
        return cfg_token

    return ""


async def _preflight_discord_check() -> tuple[bool, str]:
    """
    Предзапуск: проверяем доступность Discord API.
    
    Используем КАСТОМНЫЙ DNS-резолвер (1.1.1.1 / 8.8.8.8) — это критично,
    потому что системный DNS в РФ может быть заблокирован.
    
    Возвращает (ok, message). Если ok=False — message содержит человекочитаемую
    инструкцию что делать.
    """
    import aiohttp
    from utils.http import make_session, is_custom_dns_available
    
    test_urls = [
        ("Discord API", "https://discord.com/api/v10/gateway"),
        ("Discord CDN", "https://discord.com/api/v10/ping"),
    ]
    results = []
    
    # Используем сессию с кастомным DNS
    async with make_session(timeout=15) as session:
        for name, url in test_urls:
            try:
                async with session.get(url) as resp:
                    results.append((name, resp.status, None))
            except asyncio.TimeoutError:
                results.append((name, None, "timeout"))
            except aiohttp.ClientConnectorError as e:
                results.append((name, None, f"connect_error: {e}"))
            except Exception as e:
                results.append((name, None, f"{type(e).__name__}: {e}"))
    
    # Discord API должен ответить 200
    api_ok = results[0][1] == 200
    if api_ok:
        return True, "Discord API доступен"
    
    # Формируем человекочитаемую диагностику
    msg_lines = [
        "═══════════════════════════════════════════════════════════",
        "  ⚠️  НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ К DISCORD",
        "═══════════════════════════════════════════════════════════",
        "",
        f"  Кастомный DNS (1.1.1.1 / 8.8.8.8): "
        f"{'✅ включён' if is_custom_dns_available() else '❌ aiodns не установлен'}",
        "",
        "  Результаты проверки сети:",
    ]
    for name, status, err in results:
        if status:
            msg_lines.append(f"    {name}: HTTP {status}")
        else:
            msg_lines.append(f"    {name}: ❌ {err}")
    
    if not is_custom_dns_available():
        msg_lines.extend([
            "",
            "  ⚠️  aiodns не установлен — это критично для работы в РФ!",
            "      Установите: pip install aiodns",
            "      Без aiodns бот использует системный DNS, который может быть заблокирован.",
        ])
    
    msg_lines.extend([
        "",
        "  ═══════ ВОЗМОЖНЫЕ ПРИЧИНЫ И РЕШЕНИЯ ═══════",
        "",
        "  1️⃣  Провайдер блокирует discord.com на уровне IP",
        "     Даже с кастомным DNS соединение не пройдёт, если IP заблокирован.",
        "     Решение: используйте VPN с туннелированием (не только DNS):",
        "     • Cloudflare WARP в режиме WARP (не 1.1.1.1 only)",
        "       Settings → Advanced → Connection options → WARP",
        "     • Outline VPN / Amnezia / Wireguard / OpenVPN",
        "     • Любой SOCKS5/HTTP прокси",
        "",
        "  2️⃣  Использовать прокси прямо в боте",
        "     Задайте переменные окружения перед запуском:",
        "       set HTTPS_PROXY=http://127.0.0.1:8080",
        "       set HTTP_PROXY=http://127.0.0.1:8080",
        "       python bot.py",
        "     aiohttp автоматически подхватит прокси",
        "",
        "  3️⃣  Антивирус/брандмауэр блокирует Python",
        "     • Добавьте python.exe в исключения Windows Defender",
        "     • Проверьте Firewall",
        "",
        "  4️⃣  Запустите 'python diagnose.py' для детальной диагностики",
        "",
        "  ═══════════════════════════════════════════════════════════",
    ])
    return False, "\n".join(msg_lines)


async def _start_with_retry(bot: commands.Bot, token: str, max_attempts: int = 3):
    """
    Запускает бота с retry-логикой.
    
    Если бот не может подключиться к Discord — пробуем ещё раз через
    5/10/20 секунд (экспоненциальная задержка).
    
    Но если токен невалидный (401) — не ретраим, это бессмысленно.
    """
    import aiohttp
    import discord
    
    for attempt in range(1, max_attempts + 1):
        try:
            await bot.start(token)
            return  # если вернулись — бот завершился штатно
        except discord.LoginFailure as e:
            # 401 Unauthorized — токен невалидный, ретраить бессмысленно
            log.error("═══════════════════════════════════════════════════════════")
            log.error("  ТОКЕН НЕВАЛИДНЫЙ — Discord отклонил авторизацию")
            log.error("  Токен: %s...%s", token[:10], token[-4:])
            log.error("  Ошибка: %s", e)
            log.error("  ")
            log.error("  Проверьте:")
            log.error("    1. Скопирован ли токен полностью (без пробелов)")
            log.error("    2. Не отозван ли токен на https://discord.com/developers")
            log.error("    3. Не сброшен ли токен (Reset Token в настройках бота)")
            log.error("═══════════════════════════════════════════════════════════")
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError,
                asyncio.TimeoutError, ConnectionError, OSError) as e:
            # Сетевая ошибка — ретраим
            if attempt < max_attempts:
                wait = 5 * (2 ** (attempt - 1))  # 5, 10, 20 секунд
                log.warning(
                    "Сетевая ошибка при подключении к Discord (попытка %d/%d): %s",
                    attempt, max_attempts, e,
                )
                log.warning("Повторная попытка через %d секунд...", wait)
                await asyncio.sleep(wait)
                continue
            # Все попытки провалились
            log.error("═══════════════════════════════════════════════════════════")
            log.error("  НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ К DISCORD ЗА %d ПОПЫТКИ", max_attempts)
            log.error("  Последняя ошибка: %s: %s", type(e).__name__, e)
            log.error("═══════════════════════════════════════════════════════════")
            ok, msg = await _preflight_discord_check()
            log.error(msg)
            raise
        except KeyboardInterrupt:
            log.info("Остановка по Ctrl+C")
            return
        except Exception as e:
            log.exception("Непредвиденная ошибка при запуске бота: %s", e)
            raise


async def main():
    # Инициализируем БД до старта бота
    await database.init_db()
    log.info("База данных инициализирована.")

    if _DOTENV_AVAILABLE:
        log.info("python-dotenv обнаружён — переменные из .env загружены.")
    else:
        log.info("python-dotenv не установлен. Используются переменные окружения "
                 "только из системы/панели хостинга.")

    async with bot:
        for cog in INITIAL_COGS:
            try:
                await bot.load_extension(cog)
                log.info("Загружен ког: %s", cog)
            except Exception as e:
                log.exception("Не удалось загрузить ког %s: %s", cog, e)

        token = _resolve_token()
        if not token:
            log.error("═══════════════════════════════════════════════════════════")
            log.error("  ТОКЕН БОТА НЕ НАСТРОЕН!")
            log.error("  Способ 1 (рекомендуется): задайте переменную окружения")
            log.error("                DISCORD_TOKEN через панель хостинга,")
            log.error("                либо создайте файл .env со строкой:")
            log.error("                DISCORD_TOKEN=ваш_токен_бота")
            log.error("  Способ 2 (не рекомендуется): впишите токен в config.json")
            log.error("                в поле 'token'.")
            log.error("═══════════════════════════════════════════════════════════")
            sys.exit(1)

        # Pre-flight проверка Discord (только на Windows/локально —
        # на хостинге Discord всегда доступен)
        import platform
        if platform.system() == "Windows":
            log.info("Проверка доступности Discord API...")
            ok, msg = await _preflight_discord_check()
            if ok:
                log.info("✅ Discord API доступен. Запуск бота...")
            else:
                log.error(msg)
                log.error("")
                log.error("Бот не запущен. Исправьте сетевую проблему и попробуйте снова.")
                log.error("Подсказка: запустите 'python diagnose.py' для детальной диагностики.")
                sys.exit(1)

        # Запуск с retry-логикой
        await _start_with_retry(bot, token, max_attempts=3)


if __name__ == "__main__":
    # На Windows раньше нужен был WindowsSelectorEventLoopPolicy, чтобы aiohttp
    # корректно работал. В Python 3.14 эта политика объявлена deprecated, а
    # современный aiohttp (3.10+) отлично работает на дефолтном ProactorEventLoop.
    # Поэтому политику НЕ меняем — пусть используется системная по умолчанию.
    # Это убирает DeprecationWarning: "The WindowsSelectorEventLoopPolicy is deprecated".
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    except Exception as e:
        log.exception("Фатальная ошибка: %s", e)
        sys.exit(1)
