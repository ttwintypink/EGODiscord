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
        return json.load(f)


CONFIG = load_config()

# Подготовка директорий
os.makedirs("transcripts", exist_ok=True)
os.makedirs("logs", exist_ok=True)


# --- Bot instance -----------------------------------------------------------
intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix=".",
    intents=intents,
    help_command=None,
    case_insensitive=True,
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
        # Подсказка, если похоже на опечатку или попытку .help
        invoked = ctx.invoked_with.lower() if ctx.invoked_with else ""
        if invoked in ("help", "h", "?", "команды", "помощь"):
            embed = discord.Embed(
                title="📋 Команды EGO System",
                description=(
                    "Префикс команд: **`.`**\n\n"
                    "**🎫 Тикеты:**\n"
                    "`.setup` — установить панель тикетов *(только разработчик)*\n\n"
                    "**👑 Модерация:**\n"
                    "`.blacklist add @user` — добавить в ЧС\n"
                    "`.blacklist remove @user` — убрать из ЧС\n"
                    "`.blacklist list` — список ЧС\n"
                    "`.stats` — ТОП рекрутеров\n"
                    "`.setup-voprosy` — изменить вопросы анкеты\n\n"
                    "**📞 Для разработчика (внутри тикета):**\n"
                    "`.call <текст>` — написать кандидату в ЛС\n"
                    "`.voice` — создать голосовой канал обзвона"
                ),
                color=0x5865F2,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="EGODiscord System")
            try:
                await ctx.send(embed=embed)
            except discord.HTTPException:
                pass
        return

    if isinstance(error, commands.MissingRequiredArgument):
        try:
            await ctx.send(
                embed=discord.Embed(
                    title="⚠️ Недостаточно аргументов",
                    description=f"Команда `.{ctx.command}` требует аргументы.\n"
                                f"Использование: `.{ctx.command} {ctx.command.signature}`",
                    color=0xFEE75C,
                ).set_footer(text="EGODiscord System"),
            )
        except discord.HTTPException:
            pass
        return

    if isinstance(error, commands.CheckFailure):
        # Молча игнорируем проверки (например, guild_only)
        return

    # Прочие ошибки — логируем
    log.exception("Ошибка в команде %s: %s", ctx.command, error)
    try:
        await ctx.send(
            embed=discord.Embed(
                title="❌ Ошибка",
                description=f"Произошла ошибка при выполнении команды.\n"
                            f"```\n{type(error).__name__}: {error}\n```",
                color=0xED4245,
            ).set_footer(text="EGODiscord System"),
        )
    except discord.HTTPException:
        pass


# --- Загрузка когов ---------------------------------------------------------
INITIAL_COGS = [
    "cogs.tickets",
    "cogs.ticket_control",
    "cogs.moderation",
    "cogs.developer",
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

        await bot.start(token)


if __name__ == "__main__":
    # На Windows предотвращаем ошибку с ProactorEventLoop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    except Exception as e:
        log.exception("Фатальная ошибка: %s", e)
        sys.exit(1)
