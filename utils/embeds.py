"""
utils/embeds.py — Централизованная фабрика Embed-сообщений EGODiscord.

Все Embed-сообщения в проекте создаются через эту фабрику, чтобы
гарантировать единый стиль: цветовая палитра, футер «EGODiscord System»,
московское время в timestamp (по возможности используем discord-овский
timestamp, который сам конвертирует в локальный часовой пояс пользователя,
а в футер пишем читаемое МСК-время для логов).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

import discord

# --- Цветовая палитра -------------------------------------------------------
COLOR_MAIN    = 0x5865F2   # мягкий фиолетовый
COLOR_SUCCESS = 0x57F287   # мягкий зелёный
COLOR_ERROR   = 0xED4245   # мягкий красный
COLOR_WARNING = 0xFEE75C   # жёлтый (используем более читаемый FEE75C, а не F1C40F)

FOOTER_TEXT = "EGODiscord System"

MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    """Текущее время в МСК."""
    return datetime.now(MSK)


def msk_timestamp(dt: Optional[datetime] = None) -> str:
    """Строка времени МСК вида '03.08.2026 14:25:10 МСК'."""
    dt = dt or now_msk()
    return dt.strftime("%d.%m.%Y %H:%M:%S МСК")


def _build(color: int, title: Optional[str], description: Optional[str],
           fields: Optional[list[tuple[str, str, bool]]] = None,
           footer_text: Optional[str] = None,
           thumbnail: Optional[str] = None,
           image: Optional[str] = None,
           author_name: Optional[str] = None,
           author_icon: Optional[str] = None) -> discord.Embed:
    """Внутренний строитель Embed-а."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=now_msk(),
    )
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    footer = footer_text or FOOTER_TEXT
    embed.set_footer(text=footer)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    if author_name:
        embed.set_author(name=author_name,
                         icon_url=author_icon if author_icon else discord.Embed.Empty)
    return embed


# --- Публичные функции-фабрики ----------------------------------------------

def build_main(title: str, description: str = "",
               fields: Optional[list[tuple[str, str, bool]]] = None,
               footer_text: Optional[str] = None,
               thumbnail: Optional[str] = None) -> discord.Embed:
    return _build(COLOR_MAIN, title, description or None, fields,
                  footer_text, thumbnail)


def build_success(title: str = "✅ Успешно", description: str = "",
                  fields: Optional[list[tuple[str, str, bool]]] = None,
                  footer_text: Optional[str] = None,
                  thumbnail: Optional[str] = None) -> discord.Embed:
    return _build(COLOR_SUCCESS, title, description or None, fields,
                  footer_text, thumbnail)


def build_error(title: str = "❌ Ошибка", description: str = "",
                fields: Optional[list[tuple[str, str, bool]]] = None,
                footer_text: Optional[str] = None,
                thumbnail: Optional[str] = None) -> discord.Embed:
    return _build(COLOR_ERROR, title, description or None, fields,
                  footer_text, thumbnail)


def build_warning(title: str = "⚠️ Внимание", description: str = "",
                  fields: Optional[list[tuple[str, str, bool]]] = None,
                  footer_text: Optional[str] = None,
                  thumbnail: Optional[str] = None) -> discord.Embed:
    return _build(COLOR_WARNING, title, description or None, fields,
                  footer_text, thumbnail)


def build_info(title: str, description: str = "",
               fields: Optional[list[tuple[str, str, bool]]] = None,
               footer_text: Optional[str] = None,
               thumbnail: Optional[str] = None,
               color: Optional[int] = None) -> discord.Embed:
    """Универсальная сборка с произвольным цветом (по умолчанию Main)."""
    return _build(color or COLOR_MAIN, title, description or None, fields,
                  footer_text, thumbnail)


# --- Готовые часто используемые embed'ы -------------------------------------

def error_already_has_ticket() -> discord.Embed:
    return build_error(
        title="❌ У вас уже есть тикет",
        description="У вас уже есть открытый тикет. Сначала закройте текущий, "
                    "чтобы создать новый.",
    )


def error_blacklisted() -> discord.Embed:
    return build_error(
        title="🚫 Доступ заблокирован",
        description="Вы находитесь в чёрном списке сервера и не можете "
                    "создавать тикеты. Свяжитесь с администрацией.",
    )


def error_no_permission() -> discord.Embed:
    return build_error(
        title="🚫 Недостаточно прав",
        description="У вас нет прав на использование этой команды.",
    )


def error_not_in_ticket() -> discord.Embed:
    return build_error(
        title="❌ Не в тикете",
        description="Эту команду можно использовать только внутри канала тикета.",
    )


def success_ticket_created(username: str, ticket_type: str) -> discord.Embed:
    return build_success(
        title="🎫 Тикет создан",
        description=f"Создан тикет для пользователя **{username}** "
                    f"(тип: {'клан' if ticket_type == 'clan' else 'модерация'}).",
    )
