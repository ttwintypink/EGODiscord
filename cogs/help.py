"""
cogs/help.py — Премиальное оформление команды .help.

Команды:
    .help              — открыть красивую справку с категориями
    .help <категория>  — сразу открыть нужную категорию
                         (tickets / setup / moderation / inticket / dev)

Дизайн:
    • Главная страница — обзор бота с краткой статистикой и кнопками
    • Кнопки переключают категории — каждое нажатие обновляет embed
    • Embed строится динамически под права пользователя
      (кандидат видит только общее, админ — все команды)
    • Кнопка «Закрыть» убирает сообщение
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import ui
from discord.ext import commands

from utils import embeds
from utils.embeds import (
    build_main, build_info, msk_timestamp,
    COLOR_MAIN, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
)

log = logging.getLogger(__name__)


# ============================================================================
# Проверки прав
# ============================================================================

def _is_dev(member: discord.abc.User, config: dict) -> bool:
    return member.id == config.get("developer_id", 0)


def _is_leader(member: discord.Member, config: dict) -> bool:
    role_ids = {r.id for r in member.roles}
    roles_cfg = config.get("roles", {})
    rid = roles_cfg.get("leader")
    return bool(rid and rid in role_ids) or member.guild_permissions.administrator


def _is_admin(member: discord.Member, config: dict) -> bool:
    role_ids = {r.id for r in member.roles}
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return member.guild_permissions.administrator


def _is_staff(member: discord.Member, config: dict) -> bool:
    role_ids = {r.id for r in member.roles}
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator", "moderator", "helper"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return member.guild_permissions.administrator


# ============================================================================
# Описание команд (текст для embed'ов)
# ============================================================================

# Каждая команда: (синтаксис, описание, требуемая роль)
# role: "all" | "staff" | "admin" | "dev"

COMMANDS_TICKETS = [
    (".setup", "Установить панель тикетов в текущем канале", "dev"),
]

COMMANDS_SETUP = [
    (".editor", "Открыть интерактивный дашборд всех настроек бота "
                "(вопросы, текст панели, Steam-ключ, роли, каналы)", "admin"),
    (".editor questions", "Быстро открыть раздел вопросов анкеты", "admin"),
    (".editor panel-text", "Быстро изменить текст панели тикетов", "admin"),
    (".setup-voprosy", "Изменить вопросы анкеты (форма на 5 вопросов)", "admin"),
]

COMMANDS_MODERATION = [
    (".blacklist add <ID|@user>", "Добавить пользователя в чёрный список", "admin"),
    (".blacklist remove <ID>", "Удалить пользователя из чёрного списка", "admin"),
    (".blacklist list", "Показать всех заблокированных пользователей", "staff"),
    (".stats", "Показать ТОП-10 рекрутеров с оценками и реакцией", "staff"),
]

COMMANDS_INTICKET = [
    (".voice", "Создать голосовой канал для обзвона кандидата", "staff"),
    (".call <текст>", "Отправить кандидату сообщение в ЛС от разработчика", "dev"),
]

COMMANDS_DEV = [
    (".setup", "Установить/пересоздать панель тикетов в текущем канале", "dev"),
]


# ============================================================================
# Сборка embed'ов по категориям
# ============================================================================

def _filter_commands(commands: list[tuple[str, str, str]],
                     member: discord.Member, config: dict) -> list[tuple[str, str]]:
    """Фильтрует команды под права пользователя. Возвращает [(syntax, desc), ...]."""
    out = []
    for syntax, desc, role in commands:
        if role == "all":
            out.append((syntax, desc))
        elif role == "staff" and _is_staff(member, config):
            out.append((syntax, desc))
        elif role == "admin" and _is_admin(member, config):
            out.append((syntax, desc))
        elif role == "dev" and _is_dev(member, config):
            out.append((syntax, desc))
    return out


def _format_commands_block(commands: list[tuple[str, str]]) -> str:
    """Форматирует список команд в текстовый блок."""
    if not commands:
        return "— У вас нет доступа к командам этой категории. —"
    lines = []
    for syntax, desc in commands:
        lines.append(f"```fix\n{syntax}\n```\n{desc}")
    return "\n\n".join(lines)


def _build_main_help(member: discord.Member, config: dict) -> discord.Embed:
    """Главная страница справки — обзор + кнопки для категорий."""
    is_staff = _is_staff(member, config)
    is_admin = _is_admin(member, config)
    is_dev = _is_dev(member, config)

    embed = discord.Embed(
        title="🛡️ EGODiscord System — Справка",
        description=(
            f"## 🛡️ Добро пожаловать в EGODiscord System\n\n"
            f"Префикс команд: **`.`**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👋 Привет, **{member.mention}**!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"### 📂 Категории команд\n"
            f"Используйте кнопки ниже, чтобы посмотреть команды каждой категории.\n\n"
            f"{'🎫' if is_dev else '🔒'} **Тикеты** — установка панели тикетов"
            f"{' *(только разработчик)*' if not is_dev else ''}\n"
            f"{'⚙️' if is_admin else '🔒'} **Настройка** — редактор бота, вопросы"
            f"{' *(только админ)*' if not is_admin else ''}\n"
            f"{'🚫' if is_staff else '🔒'} **Модерация** — ЧС и статистика"
            f"{' *(только персонал)*' if not is_staff else ''}\n"
            f"{'📞' if is_staff else '🔒'} **В тикете** — команды внутри тикета"
            f"{' *(только персонал)*' if not is_staff else ''}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 **Совет:** `.help <категория>` — открыть конкретный раздел.\n"
            f"Доступные категории: `tickets`, `setup`, `moderation`, `inticket`"
            + (f", `dev`" if is_dev else "")
        ),
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )

    # Статус пользователя
    status_lines = []
    if is_dev:
        status_lines.append("👑 Разработчик")
    if is_admin:
        status_lines.append("🛡️ Администратор")
    elif is_staff:
        status_lines.append("👮 Персонал")
    else:
        status_lines.append("👤 Кандидат")

    embed.add_field(
        name="🪪 Ваш статус",
        value=" • ".join(status_lines),
        inline=False,
    )

    embed.set_thumbnail(url=member.guild.me.display_avatar.url)
    embed.set_footer(text=f"EGODiscord System • .help • {msk_timestamp()}")
    return embed


def _build_category_help(category: str, member: discord.Member,
                         config: dict) -> Optional[discord.Embed]:
    """Embed для конкретной категории."""
    cats = {
        "tickets": ("🎫 Тикеты", "Управление панелью тикетов", COMMANDS_TICKETS,
                    "Раздел доступен только разработчику бота."),
        "setup": ("⚙️ Настройка бота", "Редактор вопросов, текста панели, ключей и ролей",
                  COMMANDS_SETUP,
                  "Раздел доступен только администраторам (лидер, со-лидер, администратор)."),
        "moderation": ("🚫 Модерация", "Чёрный список и статистика рекрутеров",
                       COMMANDS_MODERATION,
                       "Раздел доступен всему персоналу (лидер, со-лидер, "
                       "администратор, модератор, хелпер)."),
        "inticket": ("📞 Внутри тикета", "Команды, доступные только в канале тикета",
                     COMMANDS_INTICKET,
                     "Раздел доступен персоналу и разработчику."),
        "dev": ("👑 Разработчику", "Скрытые команды разработчика бота", COMMANDS_DEV,
                "Раздел доступен только разработчику."),
    }

    if category not in cats:
        return None

    title, subtitle, commands_list, access_note = cats[category]
    filtered = _filter_commands(commands_list, member, config)

    if not filtered:
        embed = discord.Embed(
            title=f"{title} — нет доступа",
            description=(
                f"## 🔒 Доступ ограничен\n\n"
                f"{access_note}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Обратитесь к лидеру клана, если считаете, что доступ нужен."
            ),
            color=COLOR_ERROR,
            timestamp=embeds.now_msk(),
        )
        embed.set_footer(text=f"EGODiscord System • .help {category} • {msk_timestamp()}")
        return embed

    embed = discord.Embed(
        title=f"{title} — команды",
        description=(
            f"## {title}\n"
            f"### {subtitle}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{_format_commands_block(filtered)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )

    embed.add_field(
        name="📊 Статистика раздела",
        value=f"Доступных команд: **{len(filtered)}** из **{len(commands_list)}**",
        inline=False,
    )

    embed.set_thumbnail(url=member.guild.me.display_avatar.url)
    embed.set_footer(text=f"EGODiscord System • .help {category} • {msk_timestamp()}")
    return embed


# ============================================================================
# View с кнопками категорий
# ============================================================================

class HelpView(ui.View):
    """Кнопки переключения между категориями справки."""

    def __init__(self, member: discord.Member, config: dict, owner_id: int):
        super().__init__(timeout=180)  # 3 минуты
        self.member = member
        self.config = config
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

        # Скрываем кнопки, к которым у пользователя нет доступа
        if not _is_dev(member, config):
            self.btn_tickets.disabled = True
            self.btn_dev.disabled = True
        if not _is_admin(member, config):
            self.btn_setup.disabled = True
        if not _is_staff(member, config):
            self.btn_moderation.disabled = True
            self.btn_inticket.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Только тот, кто открыл справку, может переключать категории
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=build_info(
                    title="ℹ️ Это не ваша справка",
                    description=(
                        f"Используйте `.help`, чтобы открыть собственную справку.\n"
                        f"Справка адаптируется под права пользователя — у каждого "
                        f"свой список видимых команд."
                    ),
                ),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        """Через 3 минуты отключаем все кнопки."""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def _show_category(self, interaction: discord.Interaction, category: str):
        embed = _build_category_help(category, self.member, self.config)
        if embed is None:
            await interaction.response.send_message(
                embed=build_info(description=f"Категория `{category}` не найдена."),
                ephemeral=True,
            )
            return
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.HTTPException:
            pass

    async def _show_main(self, interaction: discord.Interaction):
        embed = _build_main_help(self.member, self.config)
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.HTTPException:
            pass

    # --- Кнопки категорий ---

    @ui.button(label="Главная", emoji="🏠",
               style=discord.ButtonStyle.success, custom_id="ego_help_home",
               row=0)
    async def btn_home(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_main(interaction)

    @ui.button(label="Тикеты", emoji="🎫",
               style=discord.ButtonStyle.primary, custom_id="ego_help_tickets",
               row=0)
    async def btn_tickets(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_category(interaction, "tickets")

    @ui.button(label="Настройка", emoji="⚙️",
               style=discord.ButtonStyle.primary, custom_id="ego_help_setup",
               row=0)
    async def btn_setup(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_category(interaction, "setup")

    @ui.button(label="Модерация", emoji="🚫",
               style=discord.ButtonStyle.primary, custom_id="ego_help_moderation",
               row=1)
    async def btn_moderation(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_category(interaction, "moderation")

    @ui.button(label="В тикете", emoji="📞",
               style=discord.ButtonStyle.primary, custom_id="ego_help_inticket",
               row=1)
    async def btn_inticket(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_category(interaction, "inticket")

    @ui.button(label="Дев", emoji="👑",
               style=discord.ButtonStyle.secondary, custom_id="ego_help_dev",
               row=1)
    async def btn_dev(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_category(interaction, "dev")

    @ui.button(label="Закрыть", emoji="✖️",
               style=discord.ButtonStyle.danger, custom_id="ego_help_close",
               row=2)
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

class Help(commands.Cog):
    """Красивая справка по командам."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    @commands.command(name="help", aliases=["h", "?", "команды", "помощь"])
    @commands.guild_only()
    async def help_cmd(self, ctx: commands.Context, category: Optional[str] = None):
        """
        Показывает красивую справку по всем командам бота.

        Использование:
            .help              — открыть главную страницу справки
            .help setup        — сразу открыть раздел «Настройка»
            .help moderation   — сразу открыть раздел «Модерация»

        Список доступных категорий: tickets, setup, moderation, inticket, dev
        """
        config = self._config()
        member = ctx.author

        # Нормализуем категорию
        category_norm = (category or "").strip().lower()
        valid_cats = {"tickets", "setup", "moderation", "inticket", "dev"}

        if category_norm and category_norm in valid_cats:
            # Сразу открываем нужную категорию
            embed = _build_category_help(category_norm, member, config)
            if embed is None:
                await ctx.send(
                    embed=build_info(description=f"Категория `{category_norm}` не найдена."),
                    delete_after=10,
                )
                return
            view = HelpView(member, config, owner_id=member.id)
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg
        else:
            # Главная страница
            if category_norm and category_norm not in valid_cats:
                # Пользователь ввёл категорию, но её нет — подсказываем
                hint = (
                    f"Категория `{category}` не найдена.\n"
                    f"Доступные категории: `tickets`, `setup`, `moderation`, `inticket`"
                    f"{', `dev`' if _is_dev(member, config) else ''}\n\n"
                    f"Показываю главную страницу справки."
                )
                embed = _build_main_help(member, config)
                # Дописываем подсказку наверх
                embed.description = f"⚠️ {hint}\n\n" + (embed.description or "")
            else:
                embed = _build_main_help(member, config)
            view = HelpView(member, config, owner_id=member.id)
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg

        # Удаляем сообщение с командой
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
