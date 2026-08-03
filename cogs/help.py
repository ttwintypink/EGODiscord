"""
cogs/help.py — Премиальное оформление команды .help с dropdown-меню.

Команды:
    .help              — открыть красивую справку с категориями
    .help <категория>  — сразу открыть нужную категорию
                         (tickets / setup / moderation / inticket / dev)

Дизайн:
    • Главная страница — обзор бота с краткой статистикой
    • Select-меню с категориями — выбираешь и embed меняется
    • Кнопки: Главная / Закрыть
    • Embed строится динамически под права пользователя
    • Каждая команда показана с синтаксисом, описанием и тегом роли
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

# Каждая команда: (синтаксис, описание, требуемая роль, тег для отображения)
# role: "all" | "staff" | "admin" | "dev"

COMMANDS_TICKETS = [
    (".setup", "Установить панель тикетов в текущем канале", "dev", "👑 Dev"),
]

COMMANDS_SETUP = [
    (".editor", "Открыть интерактивный дашборд всех настроек бота "
                "(вопросы, текст панели, Steam-ключ, роли, каналы, embed-цвет)", "admin", "🛡️ Admin"),
    (".editor questions", "Быстро открыть раздел вопросов анкеты", "admin", "🛡️ Admin"),
    (".editor panel-text", "Быстро изменить текст панели тикетов", "admin", "🛡️ Admin"),
    (".editor color", "Изменить цвет embed-сообщений бота", "admin", "🛡️ Admin"),
    (".editor reset", "Сбросить все настройки к значениям по умолчанию", "dev", "👑 Dev"),
    (".menu", "Открыть интерактивное меню быстрых действий", "admin", "🛡️ Admin"),
    (".setup-voprosy", "Изменить вопросы анкеты (форма на 5 вопросов)", "admin", "🛡️ Admin"),
]

COMMANDS_MODERATION = [
    (".blacklist add <ID|@user>", "Добавить пользователя в чёрный список", "admin", "🛡️ Admin"),
    (".blacklist remove <ID>", "Удалить пользователя из чёрного списка", "admin", "🛡️ Admin"),
    (".blacklist list", "Показать всех заблокированных пользователей", "staff", "👮 Staff"),
    (".stats", "Показать ТОП-10 рекрутеров с оценками и реакцией", "staff", "👮 Staff"),
]

COMMANDS_INTICKET = [
    (".voice", "Создать голосовой канал для собеседования кандидата", "staff", "👮 Staff"),
    (".call <текст>", "Отправить кандидату сообщение в ЛС от разработчика", "dev", "👑 Dev"),
]

COMMANDS_DEV = [
    (".setup", "Установить/пересоздать панель тикетов в текущем канале", "dev", "👑 Dev"),
    (".ping", "Проверить задержку бота до Discord API", "dev", "👑 Dev"),
    (".info", "Системная информация: Python, discord.py, сервера, аптайм", "dev", "👑 Dev"),
    (".unload <ког>", "Выгрузить ког (отключить группу команд)", "dev", "👑 Dev"),
    (".load <ког>", "Загрузить ког обратно", "dev", "👑 Dev"),
    (".reload <ког>", "Перезагрузить ког (применить изменения в коде)", "dev", "👑 Dev"),
]


# ============================================================================
# Сборка embed'ов по категориям
# ============================================================================

def _filter_commands(commands: list[tuple[str, str, str, str]],
                     member: discord.Member, config: dict) -> list[tuple[str, str, str]]:
    """Фильтрует команды под права пользователя. Возвращает [(syntax, desc, tag), ...]."""
    out = []
    for syntax, desc, role, tag in commands:
        if role == "all":
            out.append((syntax, desc, tag))
        elif role == "staff" and _is_staff(member, config):
            out.append((syntax, desc, tag))
        elif role == "admin" and _is_admin(member, config):
            out.append((syntax, desc, tag))
        elif role == "dev" and _is_dev(member, config):
            out.append((syntax, desc, tag))
    return out


def _format_commands_block(commands: list[tuple[str, str, str]]) -> str:
    """Форматирует список команд в premium-текстовый блок."""
    if not commands:
        return "🔒 *У вас нет доступа к командам этой категории.*"
    lines = []
    for syntax, desc, tag in commands:
        lines.append(f"### `{syntax}`")
        lines.append(f"{desc}")
        lines.append(f"```fix\n{syntax}\n```")
    return "\n".join(lines)


def _build_main_help(member: discord.Member, config: dict, bot: commands.Bot = None) -> discord.Embed:
    """Главная страница справки — обзор + select-меню."""
    is_staff = _is_staff(member, config)
    is_admin = _is_admin(member, config)
    is_dev = _is_dev(member, config)

    # Подсчёт доступных команд
    total_visible = 0
    total_all = 0
    for cmds in (COMMANDS_TICKETS, COMMANDS_SETUP, COMMANDS_MODERATION,
                 COMMANDS_INTICKET, COMMANDS_DEV):
        total_all += len(cmds)
        total_visible += len(_filter_commands(cmds, member, config))

    # Статус пользователя
    if is_dev:
        status_emoji = "👑"
        status_text = "Разработчик"
    elif is_admin:
        status_emoji = "🛡️"
        status_text = "Администратор"
    elif is_staff:
        status_emoji = "👮"
        status_text = "Персонал"
    else:
        status_emoji = "👤"
        status_text = "Кандидат"

    description = (
        f"## 🛡️ Добро пожаловать в EGODiscord System\n\n"
        f"👋 Привет, **{member.mention}**!\n"
        f"┌─────────────────────────────\n"
        f"│ 🪪 **Статус:** {status_emoji} {status_text}\n"
        f"│ ⚙️ **Префикс:** `.`\n"
        f"│ 📊 **Команд доступно:** {total_visible} из {total_all}\n"
        f"└─────────────────────────────\n\n"
        f"### 📂 Категории команд\n"
        f"Выберите категорию в **выпадающем меню** ниже, чтобы посмотреть команды.\n\n"
        f"{'🎫' if is_dev else '🔒'} **Тикеты** — установка панели тикетов"
        f"{' *(только разработчик)*' if not is_dev else ''}\n"
        f"{'⚙️' if is_admin else '🔒'} **Настройка** — редактор бота, вопросы, цвет"
        f"{' *(только админ)*' if not is_admin else ''}\n"
        f"{'🚫' if is_staff else '🔒'} **Модерация** — ЧС и статистика"
        f"{' *(только персонал)*' if not is_staff else ''}\n"
        f"{'📞' if is_staff else '🔒'} **В тикете** — команды внутри тикета"
        f"{' *(только персонал)*' if not is_staff else ''}\n"
        f"{'👑' if is_dev else '🔒'} **Разработчику** — скрытые команды"
        f"{' *(только dev)*' if not is_dev else ''}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 **Совет:** `.help <категория>` — открыть конкретный раздел.\n"
        f"Доступные категории: `tickets`, `setup`, `moderation`, `inticket`"
        f"{', `dev`' if is_dev else ''}"
    )

    embed = discord.Embed(
        title="🛡️ EGODiscord System — Справка",
        description=description,
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )

    # Статистика бота (если есть доступ к bot)
    if bot is not None:
        try:
            guild_count = len(bot.guilds)
            user_count = sum(g.member_count or 0 for g in bot.guilds)
            latency = f"{bot.latency * 1000:.0f} ms" if bot.latency > 0 else "—"
            embed.add_field(
                name="📡 Статистика бота",
                value=(
                    f"```fix\n"
                    f"Серверов:    {guild_count}\n"
                    f"Пользователей: {user_count}\n"
                    f"Задержка:    {latency}\n"
                    f"```"
                ),
                inline=False,
            )
        except Exception:
            pass

    embed.add_field(
        name="🚀 Быстрые действия",
        value=(
            "• **`.help setup`** — посмотреть как настроить бота\n"
            "• **`.editor`** — открыть редактор (только админ)\n"
            "• **`.menu`** — открыть меню быстрых действий (только админ)"
        ),
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
        "setup": ("⚙️ Настройка бота", "Редактор вопросов, текста панели, ключей, ролей и embed-цвета",
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
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Обратитесь к лидеру клана, если считаете, что доступ нужен."
            ),
            color=COLOR_ERROR,
            timestamp=embeds.now_msk(),
        )
        embed.set_footer(text=f"EGODiscord System • .help {category} • {msk_timestamp()}")
        return embed

    # Формируем поля — каждая команда в отдельном field
    embed = discord.Embed(
        title=f"{title} — команды",
        description=(
            f"## {title}\n"
            f"### {subtitle}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )

    for syntax, desc, tag in filtered:
        embed.add_field(
            name=f"`{syntax}`  {tag}",
            value=desc,
            inline=False,
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
# Select-меню категорий
# ============================================================================

class HelpCategorySelect(ui.Select):
    """Выпадающее меню выбора категории справки."""

    def __init__(self, member: discord.Member, config: dict):
        self.member = member
        self.config = config

        options = [
            discord.SelectOption(
                label="Главная",
                description="Обзор бота и список категорий",
                emoji="🏠",
                value="home",
            ),
            discord.SelectOption(
                label="Тикеты",
                description="Установка панели тикетов",
                emoji="🎫",
                value="tickets",
            ),
            discord.SelectOption(
                label="Настройка",
                description="Редактор бота, вопросы, цвет",
                emoji="⚙️",
                value="setup",
            ),
            discord.SelectOption(
                label="Модерация",
                description="Чёрный список и статистика",
                emoji="🚫",
                value="moderation",
            ),
            discord.SelectOption(
                label="В тикете",
                description="Команды внутри тикета",
                emoji="📞",
                value="inticket",
            ),
            discord.SelectOption(
                label="Разработчику",
                description="Скрытые команды dev",
                emoji="👑",
                value="dev",
            ),
        ]

        # Скрываем недоступные категории
        is_dev = _is_dev(member, config)
        is_admin = _is_admin(member, config)
        is_staff = _is_staff(member, config)

        filtered_options = []
        for opt in options:
            if opt.value == "tickets" and not is_dev:
                opt.description = "🔒 Только для разработчика"
            elif opt.value == "setup" and not is_admin:
                opt.description = "🔒 Только для администратора"
            elif opt.value == "moderation" and not is_staff:
                opt.description = "🔒 Только для персонала"
            elif opt.value == "inticket" and not is_staff:
                opt.description = "🔒 Только для персонала"
            elif opt.value == "dev" and not is_dev:
                opt.description = "🔒 Только для разработчика"
            filtered_options.append(opt)

        super().__init__(
            placeholder="📂 Выберите категорию для просмотра команд...",
            min_values=1,
            max_values=1,
            options=filtered_options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == "home":
            embed = _build_main_help(self.member, self.config)
        else:
            embed = _build_category_help(value, self.member, self.config)
            if embed is None:
                await interaction.response.send_message(
                    embed=build_info(description=f"Категория `{value}` не найдена."),
                    ephemeral=True,
                )
                return
        try:
            await interaction.response.edit_message(embed=embed, view=self.view)
        except discord.HTTPException:
            pass


# ============================================================================
# View с select-меню и кнопками
# ============================================================================

class HelpView(ui.View):
    """Select-меню + кнопки управления справкой."""

    def __init__(self, member: discord.Member, config: dict, owner_id: int,
                 bot: commands.Bot = None):
        super().__init__(timeout=300)  # 5 минут
        self.member = member
        self.config = config
        self.owner_id = owner_id
        self.bot = bot
        self.message: Optional[discord.Message] = None

        # Добавляем select-меню категорий
        self.select = HelpCategorySelect(member, config)
        self.add_item(self.select)

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
        """Через 5 минут отключаем все элементы."""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def _show_main(self, interaction: discord.Interaction):
        embed = _build_main_help(self.member, self.config, self.bot)
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.HTTPException:
            pass

    # --- Кнопки управления (row=1) ---

    @ui.button(label="Главная", emoji="🏠",
               style=discord.ButtonStyle.success, custom_id="ego_help_home",
               row=1)
    async def btn_home(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_main(interaction)

    @ui.button(label="Закрыть", emoji="✖️",
               style=discord.ButtonStyle.danger, custom_id="ego_help_close",
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

class Help(commands.Cog):
    """Красивая справка по командам с select-меню."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    @commands.command(name="help", aliases=["h", "?", "команды", "помощь", "хелп"])
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
            view = HelpView(member, config, owner_id=member.id, bot=self.bot)
            # Выбираем нужную категорию в select по умолчанию
            for opt in view.select.options:
                opt.default = (opt.value == category_norm)
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
                embed = _build_main_help(member, config, self.bot)
                # Дописываем подсказку наверх
                embed.description = f"⚠️ {hint}\n\n" + (embed.description or "")
            else:
                embed = _build_main_help(member, config, self.bot)
            view = HelpView(member, config, owner_id=member.id, bot=self.bot)
            # Главная выбрана по умолчанию
            for opt in view.select.options:
                opt.default = (opt.value == "home")
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg

        # Удаляем сообщение с командой
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
