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

import database
from cogs.ticket_control import (
    TicketControlView,
    CloseDecisionView,
    ConfirmCloseView,
    RatingView,
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
    bot.add_view(TicketPanelView(CONFIG))
    bot.add_view(TicketControlView(CONFIG))
    bot.add_view(CloseDecisionView(CONFIG))
    bot.add_view(ConfirmCloseView(CONFIG))
    bot.add_view(RatingView(CONFIG))
    bot.add_view(RestoreTicketView(CONFIG))

    # Запускаем фоновые задачи (если ещё не запущены)
    ticket_control_cog = bot.get_cog("TicketControl")
    if ticket_control_cog:
        ticket_control_cog.start_background_tasks()

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="за заявками EGO | .setup"
        ),
    )


# --- Загрузка когов ---------------------------------------------------------
INITIAL_COGS = [
    "cogs.tickets",
    "cogs.ticket_control",
    "cogs.moderation",
    "cogs.developer",
]


async def main():
    # Инициализируем БД до старта бота
    await database.init_db()
    log.info("База данных инициализирована.")

    async with bot:
        for cog in INITIAL_COGS:
            try:
                await bot.load_extension(cog)
                log.info("Загружен ког: %s", cog)
            except Exception as e:
                log.exception("Не удалось загрузить ког %s: %s", cog, e)

        token = CONFIG.get("token", "")
        if not token or token == "ВСТАВЬ_ТОКЕН_БОТА":
            log.error("═══════════════════════════════════════════════════════════")
            log.error("  ТОКЕН БОТА НЕ НАСТРОЕН!")
            log.error("  Откройте config.json и подставьте реальный токен в поле 'token'.")
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
