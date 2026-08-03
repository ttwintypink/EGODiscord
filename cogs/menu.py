"""
cogs/menu.py — Интерактивное меню быстрых действий для администрации.

Команда:
    .menu — открыть дашборд с быстрыми действиями:
        • Создать панель тикетов
        • Открыть редактор настроек
        • Открыть справку
        • Показать статистику бота
        • Список заблокированных
        • ТОП рекрутеров
        • Проверить задержку
        • Пересоздать панель

Меню использует select + кнопки для максимальной плотности действий.
"""
from __future__ import annotations

import logging
import platform
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import ui
from discord.ext import commands

from utils import embeds
from utils.embeds import (
    build_main, build_info, build_success, build_error,
    msk_timestamp, COLOR_MAIN, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
)

log = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))


# ============================================================================
# Проверки прав (дублируем из help.py для независимости)
# ============================================================================

def _is_dev(member: discord.abc.User, config: dict) -> bool:
    return member.id == config.get("developer_id", 0)


def _is_admin(member: discord.Member, config: dict) -> bool:
    role_ids = {r.id for r in member.roles}
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return member.guild_permissions.administrator


# ============================================================================
# Embed'ы меню
# ============================================================================

def _build_menu_embed(member: discord.Member, config: dict,
                       bot: commands.Bot = None) -> discord.Embed:
    """Главная embed меню быстрых действий."""
    is_admin = _is_admin(member, config)
    is_dev = _is_dev(member, config)

    # Статистика бота
    stats_lines = ["### 📡 Статистика бота"]
    if bot is not None:
        try:
            latency = bot.latency * 1000 if bot.latency > 0 else 0
            latency_emoji = "🟢" if latency < 100 else "🟡" if latency < 300 else "🔴"
            stats_lines.append(
                f"{latency_emoji} **Задержка:** {latency:.0f} ms\n"
                f"🌐 **Серверов:** {len(bot.guilds)}\n"
                f"👥 **Пользователей:** {sum(g.member_count or 0 for g in bot.guilds):,}\n"
                f"🤖 **Пользователь:** {bot.user.mention}"
            )
        except Exception:
            stats_lines.append("⏳ Загрузка...")
    else:
        stats_lines.append("⏳ Загрузка...")

    embed = discord.Embed(
        title="🎛️ Меню быстрых действий EGO",
        description=(
            f"## 👋 Привет, {member.mention}!\n\n"
            f"Это интерактивное меню для управления ботом.\n"
            f"Выберите действие в **выпадающем списке** ниже или нажмите кнопку.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )

    embed.add_field(
        name="📊 Статистика",
        value="\n".join(stats_lines),
        inline=False,
    )

    embed.add_field(
        name="⚡ Быстрые действия",
        value=(
            "• **Создать панель** — установить панель тикетов в этом канале\n"
            "• **Редактор** — открыть настройки бота\n"
            "• **Справка** — показать список команд\n"
            "• **Статистика рекрутеров** — ТОП-10\n"
            "• **Чёрный список** — заблокированные пользователи\n"
            "• **Информация о системе** — Python, discord.py, аптайм"
        ),
        inline=False,
    )

    embed.set_thumbnail(url=member.guild.me.display_avatar.url)
    embed.set_footer(text=f"EGODiscord System • .menu • {msk_timestamp()}")
    return embed


def _build_system_info_embed(bot: commands.Bot) -> discord.Embed:
    """Информация о системе бота."""
    try:
        import discord as d
        latency = bot.latency * 1000 if bot.latency > 0 else 0
        uptime_sec = time.time() - _get_start_time()
        uptime_str = _format_uptime(uptime_sec)

        embed = discord.Embed(
            title="ℹ️ Информация о системе",
            description=(
                f"## 🤖 EGODiscord System\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=COLOR_MAIN,
            timestamp=embeds.now_msk(),
        )

        embed.add_field(
            name="🐍 Python",
            value=(
                f"```fix\n"
                f"Версия:       {sys.version.split()[0]}\n"
                f"Платформа:    {platform.system()} {platform.release()}\n"
                f"Архитектура:  {platform.machine()}\n"
                f"```"
            ),
            inline=False,
        )

        embed.add_field(
            name="📚 Библиотеки",
            value=(
                f"```fix\n"
                f"discord.py:   {d.__version__}\n"
                f"```"
            ),
            inline=False,
        )

        embed.add_field(
            name="🤖 Бот",
            value=(
                f"```fix\n"
                f"Пользователь: {bot.user}\n"
                f"ID:           {bot.user.id}\n"
                f"Серверов:     {len(bot.guilds)}\n"
                f"Пользователей: {sum(g.member_count or 0 for g in bot.guilds):,}\n"
                f"Задержка:     {latency:.0f} ms\n"
                f"Аптайм:       {uptime_str}\n"
                f"```"
            ),
            inline=False,
        )

        # Список загруженных когов
        loaded_cogs = list(bot.cogs.keys())
        embed.add_field(
            name="📦 Загруженные модули",
            value=f"```fix\n{', '.join(loaded_cogs)}```" if loaded_cogs else "—",
            inline=False,
        )

        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.set_footer(text=f"EGODiscord System • {msk_timestamp()}")
        return embed
    except Exception as e:
        log.exception("Ошибка в _build_system_info_embed: %s", e)
        return build_error(description=f"Ошибка сбора информации: `{e}`")


# ============================================================================
# Утилиты
# ============================================================================

_START_TIME = time.time()


def _get_start_time() -> float:
    """Возвращает время старта бота (для расчёта аптайма)."""
    return _START_TIME


def _format_uptime(seconds: float) -> str:
    """Форматирует аптайм в человекочитаемый вид."""
    if seconds < 60:
        return f"{int(seconds)} сек"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин {int(seconds % 60)} сек"
    if seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h} ч {m} мин"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    return f"{d} д {h} ч"


# ============================================================================
# Select-меню действий
# ============================================================================

class MenuActionSelect(ui.Select):
    """Выпадающее меню быстрых действий."""

    def __init__(self, member: discord.Member, config: dict, bot: commands.Bot):
        self.member = member
        self.config = config
        self.bot = bot

        options = [
            discord.SelectOption(
                label="Создать панель тикетов",
                description="Установить панель в текущем канале",
                emoji="🎫",
                value="create_panel",
            ),
            discord.SelectOption(
                label="Открыть редактор",
                description="Настройки бота (вопросы, цвет, роли)",
                emoji="🛠️",
                value="editor",
            ),
            discord.SelectOption(
                label="Показать справку",
                description="Список всех команд",
                emoji="📖",
                value="help",
            ),
            discord.SelectOption(
                label="Статистика рекрутеров",
                description="ТОП-10 рекрутеров",
                emoji="📊",
                value="stats",
            ),
            discord.SelectOption(
                label="Чёрный список",
                description="Заблокированные пользователи",
                emoji="🚫",
                value="blacklist",
            ),
            discord.SelectOption(
                label="Информация о системе",
                description="Python, discord.py, аптайм",
                emoji="ℹ️",
                value="sysinfo",
            ),
            discord.SelectOption(
                label="Проверить задержку",
                description="Pinging Discord API",
                emoji="🏓",
                value="ping",
            ),
            discord.SelectOption(
                label="Обновить меню",
                description="Перезагрузить это сообщение",
                emoji="🔄",
                value="refresh",
            ),
        ]

        # Скрываем недоступные для не-админов
        is_admin = _is_admin(member, config)
        is_dev = _is_dev(member, config)

        if not is_admin and not is_dev:
            # Обычный кандидат — только help и ping
            options = [o for o in options if o.value in ("help", "ping", "refresh")]

        super().__init__(
            placeholder="⚡ Выберите быстрое действие...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        view: "MenuView" = self.view  # type: ignore

        if value == "create_panel":
            await view._action_create_panel(interaction)
        elif value == "editor":
            await view._action_open_editor(interaction)
        elif value == "help":
            await view._action_show_help(interaction)
        elif value == "stats":
            await view._action_show_stats(interaction)
        elif value == "blacklist":
            await view._action_show_blacklist(interaction)
        elif value == "sysinfo":
            await view._action_show_sysinfo(interaction)
        elif value == "ping":
            await view._action_ping(interaction)
        elif value == "refresh":
            embed = _build_menu_embed(self.member, self.config, self.bot)
            try:
                await interaction.response.edit_message(embed=embed, view=self.view)
            except discord.HTTPException:
                pass


# ============================================================================
# View меню
# ============================================================================

class MenuView(ui.View):
    """Полное меню быстрых действий: select + кнопки."""

    def __init__(self, member: discord.Member, config: dict,
                 owner_id: int, bot: commands.Bot):
        super().__init__(timeout=300)  # 5 минут
        self.member = member
        self.config = config
        self.owner_id = owner_id
        self.bot = bot
        self.message: Optional[discord.Message] = None

        # Добавляем select-меню действий
        self.action_select = MenuActionSelect(member, config, bot)
        self.add_item(self.action_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=build_info(
                    title="ℹ️ Это не ваше меню",
                    description="Используйте `.menu`, чтобы открыть собственное меню.",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    # --- Обработчики действий ---

    async def _action_create_panel(self, interaction: discord.Interaction):
        """Создаёт панель тикетов в текущем канале."""
        from cogs.tickets import TicketPanelView, _build_panel_embed  # type: ignore
        embed = _build_panel_embed(self.config)
        view = TicketPanelView(self.config)
        try:
            await interaction.channel.send(embed=embed, view=view)
            await interaction.response.send_message(
                embed=build_success(
                    title="✅ Панель создана",
                    description=f"Панель тикетов отправлена в {interaction.channel.mention}.",
                ),
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=build_error(description=f"Не удалось создать панель: `{e}`"),
                ephemeral=True,
            )

    async def _action_open_editor(self, interaction: discord.Interaction):
        """Открывает редактор настроек (если есть права)."""
        if not _is_admin(interaction.user, self.config):
            await interaction.response.send_message(
                embed=build_error(description="Открывать редактор могут только администраторы."),
                ephemeral=True,
            )
            return
        from cogs.editor import EditorDashboardView, _dashboard_embed  # type: ignore
        view = EditorDashboardView(self.config, interaction.user.id)
        msg = await interaction.channel.send(
            embed=_dashboard_embed(self.config), view=view
        )
        view.message = msg
        await interaction.response.send_message(
            embed=build_success(description=f"Редактор открыт в {interaction.channel.mention}."),
            ephemeral=True,
        )

    async def _action_show_help(self, interaction: discord.Interaction):
        """Показывает справку."""
        from cogs.help import _build_main_help, HelpView  # type: ignore
        embed = _build_main_help(interaction.user, self.config, self.bot)
        help_view = HelpView(interaction.user, self.config, interaction.user.id, self.bot)
        for opt in help_view.select.options:
            opt.default = (opt.value == "home")
        sent_msg = await interaction.channel.send(embed=embed, view=help_view)
        help_view.message = sent_msg
        await interaction.response.send_message(
            embed=build_success(description="Справка открыта ниже."),
            ephemeral=True,
        )

    async def _action_show_stats(self, interaction: discord.Interaction):
        """Показывает статистику рекрутеров."""
        stats_cog = self.bot.get_cog("Moderation")
        if stats_cog and hasattr(stats_cog, "stats_cmd"):
            # Просто показываем что команда существует — выполним её через ctx-обёртку
            await interaction.response.send_message(
                embed=build_info(
                    title="📊 Статистика рекрутеров",
                    description=(
                        "Используйте команду `.stats` в канале, чтобы посмотреть "
                        "ТОП-10 рекрутеров с количеством принятых/отклонённых "
                        "и средней оценкой."
                    ),
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=build_info(description="Модуль модерации не загружен."),
                ephemeral=True,
            )

    async def _action_show_blacklist(self, interaction: discord.Interaction):
        """Показывает чёрный список."""
        await interaction.response.send_message(
            embed=build_info(
                title="🚫 Чёрный список",
                description=(
                    "Используйте команду `.blacklist list` в канале, чтобы "
                    "посмотреть всех заблокированных пользователей."
                ),
            ),
            ephemeral=True,
        )

    async def _action_show_sysinfo(self, interaction: discord.Interaction):
        """Показывает системную информацию."""
        embed = _build_system_info_embed(self.bot)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _action_ping(self, interaction: discord.Interaction):
        """Проверяет задержку бота."""
        latency = self.bot.latency * 1000 if self.bot.latency > 0 else 0
        emoji = "🟢" if latency < 100 else "🟡" if latency < 300 else "🔴"
        embed = discord.Embed(
            title="🏓 Pong!",
            description=(
                f"## {emoji} Задержка до Discord API\n\n"
                f"```fix\n{latency:.0f} ms\n```\n"
                f"{'✅ Отличная задержка' if latency < 100 else '⚠️ Нормальная задержка' if latency < 300 else '❌ Высокая задержка'}"
            ),
            color=COLOR_SUCCESS if latency < 100 else COLOR_WARNING if latency < 300 else COLOR_ERROR,
            timestamp=embeds.now_msk(),
        )
        embed.set_footer(text=f"EGODiscord System • {msk_timestamp()}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Кнопки (row=1) ---

    @ui.button(label="Обновить", emoji="🔄",
               style=discord.ButtonStyle.secondary, custom_id="ego_menu_refresh",
               row=1)
    async def btn_refresh(self, interaction: discord.Interaction, button: ui.Button):
        embed = _build_menu_embed(self.member, self.config, self.bot)
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.HTTPException:
            pass

    @ui.button(label="Закрыть", emoji="✖️",
               style=discord.ButtonStyle.danger, custom_id="ego_menu_close",
               row=1)
    async def btn_close(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer()
            if self.message:
                await self.message.delete()
        except discord.HTTPException:
            pass


# ============================================================================
# Cog
# ============================================================================

class MenuCog(commands.Cog):
    """Интерактивное меню быстрых действий."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    @commands.command(name="menu", aliases=["меню", "m", "dashboard", "дашборд"])
    @commands.guild_only()
    async def menu_cmd(self, ctx: commands.Context):
        """
        Открыть интерактивное меню быстрых действий.

        Меню позволяет одним кликом:
        - Создать панель тикетов
        - Открыть редактор настроек
        - Показать справку
        - Посмотреть статистику
        - Проверить задержку
        - И многое другое
        """
        config = self._config()
        member = ctx.author

        view = MenuView(member, config, owner_id=member.id, bot=self.bot)
        embed = _build_menu_embed(member, config, self.bot)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

        # Удаляем сообщение с командой
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(MenuCog(bot))
