"""
cogs/help.py — Понятная справка по командам EGO (для чайников).

Структура:
    .help              — главная страница (что умеет бот)
    .help <категория>  — сразу открыть нужный раздел

Категории:
    🏠 Главная     — обзор для всех
    🛡️ Начало      — как подать заявку (для кандидатов)
    ⚙️ Настройка    — как настроить бота (для админов)
    🚫 Модерация   — чёрный список, статистика
    📞 В тикете     — команды внутри тикета
    👑 Разработчику — служебные команды

Дизайн:
    • Простой язык, без жаргона
    • Конкретные примеры для каждой команды
    • Адаптация под права пользователя
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
# Описание команд — формат: (синтаксис, описание, пример, требуемая роль)
# ============================================================================

# Главная — для всех
COMMANDS_HOME = [
    (".help", "Открыть это меню справки", ".help", "all"),
    (".help <категория>", "Сразу открыть нужный раздел",
     ".help setup", "all"),
    (".rules", "Показать правила клана EGO в текущем канале",
     ".rules", "all"),
    (".setuprules", "Установить и закрепить правила в канале (для лидерства)",
     ".setuprules  —  в выделенном канале #правила", "admin"),
    (".menu", "Открыть меню быстрых действий (если есть права)",
     ".menu", "admin"),
]

# Начало — для кандидатов
COMMANDS_START = [
    ("Панель тикетов", "Открой канал с панелью и выбери «Клан» или «Модерация» в меню",
     "Найти канал с панелью → выбрать категорию → заполнить анкету", "all"),
    ("Заполнение анкеты", "Отвечай на вопросы честно — модератор видит ответы",
     "Например: «18 лет» — пишешь в поле только число «18»", "all"),
    ("Ожидание", "После отправки анкеты ждите — модератор откликнется в тикете",
     "Не закрывайте Discord, следите за уведомлениями", "all"),
]

# Настройка — для админов
COMMANDS_SETUP = [
    (".editor", "Открыть редактор всех настроек (dropdown-меню)",
     ".editor", "admin"),
    (".editor → Вопросы — Клан", "Изменить вопросы анкеты для клана (до 15 вопросов)",
     ".editor → выбрать «Вопросы — Клан»", "admin"),
    (".editor → Вопросы — Модерация", "Изменить вопросы анкеты для модерации",
     ".editor → выбрать «Вопросы — Модерация»", "admin"),
    (".editor → Steam API ключ", "Вставить ключ Steam (для проверки VAC-банов)",
     ".editor → выбрать «Steam API ключ»", "admin"),
    (".editor → Пинг-роли", "Настроить какие роли пинговать при создании тикета",
     ".editor → «Пинг-роли» → ввести ID через запятую", "admin"),
    (".editor → Роли персонала", "Указать ID ролей лидер/админ/модер/хелпер",
     ".editor → «Роли персонала»", "admin"),
    (".editor → Каналы и категории", "Указать ID каналов логов, категорий тикетов, роли EGO",
     ".editor → «Каналы и категории»", "admin"),
    (".editor → Цвет embed", "Изменить цвет сообщений бота (8 пресетов + HEX)",
     ".editor → «Цвет embed» → ego / red / green / gold", "admin"),
    (".editor → Приветствие", "Свой текст приветствия в тикете",
     ".editor → «Приветствие в тикете»", "admin"),
    (".editor → Брендинг", "Свой URL иконки для embed'ов бота",
     ".editor → «Брендинг (иконка)»", "admin"),
    (".editor → Превью панели", "Посмотреть как выглядит панель сейчас",
     ".editor → «Превью панели»", "admin"),
    (".editor → Пересоздать панель", "Удалить старую панель и создать новую",
     ".editor → «Пересоздать панель»", "admin"),
    (".editor → Сбросить", "Сбросить вопросы/текст/цвет (ID каналов сохранятся)",
     ".editor → «Сбросить настройки»", "admin"),
]

# Модерация
COMMANDS_MODERATION = [
    (".blacklist add <ID|@user>", "Заблокировать пользователя — не сможет создавать тикеты",
     ".blacklist add 123456789012345678", "admin"),
    (".blacklist remove <ID>", "Разблокировать пользователя",
     ".blacklist remove 123456789012345678", "admin"),
    (".blacklist list", "Показать всех заблокированных",
     ".blacklist list", "staff"),
    (".stats", "Показать ТОП-10 рекрутеров с оценками и реакцией",
     ".stats", "staff"),
]

# Внутри тикета
COMMANDS_INTICKET = [
    ("🤝 Взять в работу", "Принять тикет на себя — другим модераторам он закроется",
     "Нажать кнопку в закреплённом сообщении", "staff"),
    ("🎙️ Обзвон", "Создать голосовой канал для собеседования кандидата",
     "Нажать кнопку «Обзвон» в закреплённом сообщении", "staff"),
    ("🔇 Заглушить", "Замьютить кандидата в голосовом канале обзвона",
     "Нажать кнопку «Заглушить» (после обзвона)", "staff"),
    ("🔒 Закрыть", "Принять или отклонить заявку с указанием причины",
     "Нажать «Закрыть» → выбрать Принять/Отклонить → описать причину", "staff"),
    ("⭐ Оценка", "После закрытия кандидат получает звёздочки для оценки модератора",
     "Кандидат жмёт 1-5 звёзд в ЛС", "all"),
]

# Разработчику
COMMANDS_DEV = [
    (".setup", "Открыть интерактивное меню настройки бота (dropdown)",
     ".setup → выбрать действие из списка", "dev"),
    (".ping", "Проверить задержку бота до Discord API",
     ".ping", "dev"),
    (".info", "Системная информация: Python, discord.py, сервера, аптайм",
     ".info", "dev"),
    (".unload <ког>", "Выгрузить ког (отключить группу команд)",
     ".unload tickets", "dev"),
    (".load <ког>", "Загрузить ког обратно",
     ".load tickets", "dev"),
    (".reload <ког>", "Перезагрузить ког (применить изменения в коде)",
     ".reload tickets", "dev"),
]


# ============================================================================
# Сборка embed'ов
# ============================================================================

def _filter_commands(commands: list[tuple[str, str, str, str]],
                     member: discord.Member, config: dict) -> list[tuple[str, str, str]]:
    """Фильтрует команды под права пользователя."""
    out = []
    for syntax, desc, example, role in commands:
        if role == "all":
            out.append((syntax, desc, example))
        elif role == "staff" and _is_staff(member, config):
            out.append((syntax, desc, example))
        elif role == "admin" and _is_admin(member, config):
            out.append((syntax, desc, example))
        elif role == "dev" and _is_dev(member, config):
            out.append((syntax, desc, example))
    return out


def _build_main_help(member: discord.Member, config: dict,
                     bot: commands.Bot = None) -> discord.Embed:
    """Главная страница — обзор для чайников."""
    is_staff = _is_staff(member, config)
    is_admin = _is_admin(member, config)
    is_dev = _is_dev(member, config)

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

    # Короткий обзор что умеет бот
    description = (
        f"## 🛡️ EGODiscord System — Справка\n\n"
        f"Привет, **{member.mention}**! Я бот для системы набора в клан EGO.\n\n"
        f"### 📌 Что я умею (кратко)\n"
        f"• 🎫 **Создаю тикеты** для заявок в клан и модерацию\n"
        f"• 🛡️ **Проверяю Steam** — VAC-баны, часы в Rust, страна\n"
        f"• 🤝 **Распределяю заявки** между модераторами (claim)\n"
        f"• 🎙️ **Создаю голосовые каналы** для обзвона\n"
        f"• 📊 **Считаю статистику** рекрутеров и оценки\n"
        f"• 📜 **Сохраняю историю** тикетов (HTML-транскрипты)\n"
        f"• 🔄 **Меняю ник** кандидата на «Steam | Имя» при заявке\n\n"
        f"### 👤 Ваш статус\n"
        f"```\n"
        f"Статус:   {status_emoji} {status_text}\n"
        f"Префикс:  .  (точка)\n"
        f"```\n\n"
        f"### 📂 Категории команд\n"
        f"Выберите раздел в **меню ниже**:\n\n"
        f"🏠 **Главная** — эта страница\n"
        f"🛡️ **Начало** — как подать заявку (для кандидатов)\n"
        f"{'⚙️ **Настройка** — как настроить бота *(для админов)*' if is_admin else '🔒 **Настройка** — *(только для админов)*'}\n"
        f"{'🚫 **Модерация** — чёрный список, статистика *(для персонала)*' if is_staff else '🔒 **Модерация** — *(только для персонала)*'}\n"
        f"{'📞 **В тикете** — что делать внутри тикета *(для персонала)*' if is_staff else '🔒 **В тикете** — *(только для персонала)*'}\n"
        f"{'👑 **Разработчику** — скрытые команды' if is_dev else '🔒 **Разработчику** — *(только для разработчика)*'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 **Совет:** `.help setup` — открыть конкретный раздел сразу."
    )

    embed = discord.Embed(
        title="🛡️ EGODiscord System — Справка",
        description=description,
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )

    # Статистика бота
    if bot is not None:
        try:
            guild_count = len(bot.guilds)
            user_count = sum(g.member_count or 0 for g in bot.guilds)
            latency = f"{bot.latency * 1000:.0f} ms" if bot.latency > 0 else "—"
            embed.add_field(
                name="📡 Статистика бота",
                value=(
                    f"```\n"
                    f"Серверов:     {guild_count}\n"
                    f"Пользователей: {user_count}\n"
                    f"Задержка:     {latency}\n"
                    f"```"
                ),
                inline=False,
            )
        except Exception:
            pass

    embed.set_thumbnail(url=member.guild.me.display_avatar.url)
    embed.set_footer(text=f"EGODiscord System • .help • {msk_timestamp()}")
    return embed


def _build_category_help(category: str, member: discord.Member,
                         config: dict) -> Optional[discord.Embed]:
    """Embed для конкретной категории."""
    cats = {
        "home": ("🏠 Главная", "Обзор бота и список категорий",
                  COMMANDS_HOME, "Доступно всем."),
        "start": ("🛡️ Начало", "Как подать заявку (для кандидатов)",
                  COMMANDS_START, "Доступно всем."),
        "setup": ("⚙️ Настройка бота", "Как настроить бота — для администраторов",
                  COMMANDS_SETUP,
                  "Раздел доступен только администраторам (лидер, со-лидер, администратор)."),
        "moderation": ("🚫 Модерация", "Чёрный список и статистика рекрутеров",
                       COMMANDS_MODERATION,
                       "Раздел доступен всему персоналу (лидер, со-лидер, "
                       "администратор, модератор, хелпер)."),
        "inticket": ("📞 Внутри тикета", "Что делать внутри тикета",
                     COMMANDS_INTICKET,
                     "Раздел доступен персоналу и кандидату."),
        "dev": ("👑 Разработчику", "Скрытые команды разработчика бота",
                COMMANDS_DEV, "Раздел доступен только разработчику."),
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

    # Спец-формат для раздела "Начало" — там не команды, а инструкции
    if category == "start":
        description = (
            f"## {title}\n"
            f"### {subtitle}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        for syntax, desc, example in filtered:
            description += f"### {syntax}\n{desc}\n\n"
            description += f"**Пример:** {example}\n\n"
            description += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        description += (
            "💡 **Если что-то непонятно** — спроси в чате или у модератора. "
            "Мы поможем!"
        )
        embed = discord.Embed(
            title=f"{title} — инструкция",
            description=description,
            color=COLOR_MAIN,
            timestamp=embeds.now_msk(),
        )
    elif category == "inticket":
        # Спец-формат для команд в тикете — там действия
        description = (
            f"## {title}\n"
            f"### {subtitle}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Все действия выполняются кнопками в **закреплённом сообщении** "
            f"тикет-канала. Не нужно писать команды — просто нажимай кнопки.\n\n"
        )
        for syntax, desc, example in filtered:
            description += f"### {syntax}\n{desc}\n\n"
            description += f"**Как сделать:** {example}\n\n"
            description += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        embed = discord.Embed(
            title=f"{title} — действия",
            description=description,
            color=COLOR_MAIN,
            timestamp=embeds.now_msk(),
        )
    else:
        # Стандартный формат — команды с примерами
        description = (
            f"## {title}\n"
            f"### {subtitle}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        for syntax, desc, example in filtered:
            description += f"### `{syntax}`\n{desc}\n\n"
            description += f"**Пример:** `{example}`\n\n"
            description += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        description += f"📊 **Доступных команд:** {len(filtered)} из {len(commands_list)}"
        embed = discord.Embed(
            title=f"{title} — команды",
            description=description,
            color=COLOR_MAIN,
            timestamp=embeds.now_msk(),
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
                label="Начало (для кандидатов)",
                description="Как подать заявку — пошагово",
                emoji="🛡️",
                value="start",
            ),
            discord.SelectOption(
                label="Настройка",
                description="Как настроить бота (для админов)",
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
                description="Что делать внутри тикета",
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
            if opt.value == "setup" and not is_admin:
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
            embed = _build_main_help(self.member, self.config, self.view.bot
                                      if hasattr(self.view, "bot") else None)
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
        super().__init__(timeout=300)
        self.member = member
        self.config = config
        self.owner_id = owner_id
        self.bot = bot
        self.message: Optional[discord.Message] = None

        # Добавляем select-меню категорий
        self.select = HelpCategorySelect(member, config)
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=build_info(
                    title="ℹ️ Это не ваша справка",
                    description=(
                        f"Используйте `.help`, чтобы открыть собственную справку.\n"
                        f"Справка адаптируется под права — у каждого свой список команд."
                    ),
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
    """Понятная справка по командам бота."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    @commands.command(name="help", aliases=["h", "?", "команды", "помощь", "хелп"])
    @commands.guild_only()
    async def help_cmd(self, ctx: commands.Context, category: Optional[str] = None):
        """
        Показывает понятную справку по всем командам бота.

        Использование:
            .help              — открыть главную страницу
            .help start        — как подать заявку (для кандидатов)
            .help setup        — как настроить бота (для админов)
            .help moderation   — чёрный список и статистика
            .help inticket     — что делать в тикете
            .help dev          — служебные команды (только для разработчика)

        Категории: home, start, setup, moderation, inticket, dev
        """
        config = self._config()
        member = ctx.author

        # Нормализуем категорию
        category_norm = (category or "").strip().lower()
        # Синонимы
        aliases = {
            "tickets": "setup",  # обратная совместимость
            "main": "home",
            "кандидат": "start",
            "кандидату": "start",
            "начало": "start",
            "настройка": "setup",
            "модерация": "moderation",
            "тикет": "inticket",
            "втикете": "inticket",
            "in-ticket": "inticket",
        }
        if category_norm in aliases:
            category_norm = aliases[category_norm]

        valid_cats = {"home", "start", "setup", "moderation", "inticket", "dev"}

        if category_norm and category_norm in valid_cats:
            embed = _build_category_help(category_norm, member, config)
            if embed is None:
                await ctx.send(
                    embed=build_info(description=f"Категория `{category_norm}` не найдена."),
                    delete_after=10,
                )
                return
            view = HelpView(member, config, owner_id=member.id, bot=self.bot)
            for opt in view.select.options:
                opt.default = (opt.value == category_norm)
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg
        else:
            if category_norm and category_norm not in valid_cats:
                hint = (
                    f"⚠️ Категория `{category}` не найдена.\n"
                    f"Доступные категории: `home`, `start`, `setup`, "
                    f"`moderation`, `inticket`{', `dev`' if _is_dev(member, config) else ''}\n\n"
                    f"Показываю главную страницу справки."
                )
                embed = _build_main_help(member, config, self.bot)
                embed.description = f"{hint}\n\n" + (embed.description or "")
            else:
                embed = _build_main_help(member, config, self.bot)
            view = HelpView(member, config, owner_id=member.id, bot=self.bot)
            for opt in view.select.options:
                opt.default = (opt.value == "home")
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
