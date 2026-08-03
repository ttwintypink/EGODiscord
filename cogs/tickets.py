"""
cogs/tickets.py — Создание тикетов: панель, dropdown, модал, Steam-проверка.

Логика:
    .setup            — установка панели (только для developer_id)
    Dropdown          — выбор «клан» / «модерация»
    Modal             — анкета с вопросами из config.json
    Создание канала   — изоляция прав, пинг ролей, закрепление анкеты
    Steam-проверка    — автоматический запрос к Steam API, цветной Embed
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import ui, AllowedMentions
from discord.ext import commands

import database
from utils import embeds
from utils.embeds import (
    build_main, build_success, build_error, build_warning, build_info,
    msk_timestamp, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
)
from utils.steam_api import check_steam_account

log = logging.getLogger(__name__)


# ============================================================================
# Модальное окно анкеты
# ============================================================================

class ApplicationModal(ui.Modal, title="📝 Анкета EGO"):
    """
    Динамически создаётся из списка вопросов (макс. 5 вопросов).
    """

    # Заполняем __init__ динамически
    def __init__(self, ticket_type: str, config: dict, user: discord.Member):
        self.ticket_type = ticket_type  # 'clan' | 'mod'
        self.config = config
        self.user = user

        questions_key = "questions_clan" if ticket_type == "clan" else "questions_mod"
        questions = config.get(questions_key, [])[:5]
        if not questions:
            questions = ["Ваш SteamID или ссылка на профиль Steam?"]

        # Динамически создаём поля (макс 5 в discord.py Modal)
        self._inputs = []
        title = "📝 Заявка в клан EGO" if ticket_type == "clan" else "📝 Заявка в модерацию EGO"
        super().__init__(title=title[:45])

        for i, q in enumerate(questions):
            inp = ui.TextInput(
                label=q[:45],
                placeholder=f"Введите ответ..." if i > 0 or "steam" not in q.lower() else "7656119... или https://steamcommunity.com/...",
                required=True,
                style=discord.TextStyle.paragraph if len(q) > 30 else discord.TextStyle.short,
                max_length=500,
            )
            self._inputs.append(inp)
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message(
                embed=build_success(
                    title="✅ Анкета отправлена",
                    description="Создаю канал тикета, подождите...",
                ),
                ephemeral=True,
            )
        except discord.HTTPException:
            pass

        # Собираем ответы (обращаемся к .label через data, т.к. в новых версиях
        # discord.py TextInput.label помечен как deprecated)
        answers = []
        for inp in self._inputs:
            # Безопасное получение label: через data dict, через _label, или fallback
            label = getattr(inp, "_label", None) or inp.data.get("label") or "Вопрос"
            answers.append((label, inp.value))
        await self._create_ticket(interaction, answers)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Ошибка в ApplicationModal: %s", error)
        try:
            await interaction.followup.send(
                embed=build_error(description=f"Произошла ошибка: `{error}`"),
                ephemeral=True,
            )
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------------
    # Создание канала тикета
    # ------------------------------------------------------------------------

    async def _create_ticket(self, interaction: discord.Interaction,
                             answers: list[tuple[str, str]]):
        guild = interaction.guild
        user = self.user
        config = self.config

        # 1. Категория
        category_id = (config["category_clan_id"] if self.ticket_type == "clan"
                       else config["category_mod_id"])
        category = guild.get_channel(category_id)
        if category is None or not isinstance(category, discord.CategoryChannel):
            log.error("Категория %s не найдена!", category_id)
            await interaction.followup.send(
                embed=build_error(description="Категория для тикетов не найдена. "
                                              "Обратитесь к администратору."),
                ephemeral=True,
            )
            return

        # 2. Название канала
        safe_name = "".join(c for c in user.name if c.isalnum() or c in "_-")[:20] or "user"
        channel_name = f"🎫-ticket-{safe_name}"

        # 3. Права: закрыто для @everyone, открыто кандидату и боту
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                manage_channels=True, read_message_history=True,
                manage_permissions=True, attach_files=True, embed_links=True,
            ),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True,
            ),
        }

        # Высшей администрации тоже доступ
        roles_cfg = config.get("roles", {})
        for role_key in ("leader", "co_leader", "administrator", "moderator", "helper"):
            rid = roles_cfg.get(role_key)
            if rid:
                role = guild.get_role(rid)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True,
                        read_message_history=True, manage_channels=True,
                    )

        # 4. Создаём канал
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Тикет: {user} ({user.id}) | Тип: {self.ticket_type} | "
                      f"Создан: {msk_timestamp()}",
                reason=f"Создание тикета для {user} ({self.ticket_type})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=build_error(description="У бота нет прав на создание каналов."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=build_error(description=f"Ошибка создания канала: `{e}`"),
                ephemeral=True,
            )
            return

        # 5. Сохраняем в БД
        form_text = "\n".join(f"**{q}**\n{a}" for q, a in answers)
        await database.ticket_create(channel.id, user.id, self.ticket_type, form_text)

        # 6. Приветственное сообщение + пинг ролей
        ping_role_ids = (config.get("ping_roles_clan", []) if self.ticket_type == "clan"
                         else config.get("ping_roles_mod", []))
        ping_str = " ".join(f"<@&{rid}>" for rid in ping_role_ids) if ping_role_ids else ""

        hello_embed = build_main(
            title=f"{'🛡️' if self.ticket_type == 'clan' else '👑'} Тикет создан",
            description=(
                f"## Привет, {user.mention}! 👋\n\n"
                f"Тип заявки: **{'🛡️ Набор в клан EGO' if self.ticket_type == 'clan' else '👑 Набор в модерацию EGO'}**\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 **Что дальше?**\n"
                f"• Ознакомься со своей анкетой ниже (она закреплена)\n"
                f"• Ожидай голосового обзвона от рекрутёра\n"
                f"• Будь готов ответить на дополнительные вопросы\n\n"
                f"⏳ Обычно ответ занимает **несколько минут**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            fields=[
                ("👤 Кандидат", f"{user.mention}\n`{user.id}`", True),
                ("📂 Тип заявки", "🛡️ Клан" if self.ticket_type == "clan" else "👑 Модерация", True),
                ("📅 Создан", msk_timestamp(), True),
            ],
            footer_text="EGODiscord System • Не закрывай тикет, ожидай ответа",
        )

        # Кнопки управления
        from cogs.ticket_control import TicketControlView
        control_view = TicketControlView(config)

        content_parts = [f"{user.mention}", ping_str] if ping_str else [f"{user.mention}"]
        content = " ".join(p for p in content_parts if p)

        try:
            hello_msg = await channel.send(
                content=content,
                embed=hello_embed,
                view=control_view,
                allowed_mentions=AllowedMentions(
                    users=True, roles=True, everyone=False
                ),
            )
            await hello_msg.pin()
        except discord.HTTPException as e:
            log.warning("Не удалось отправить/закрепить приветствие: %s", e)

        # 7. Анкета — Embed с ответами, закрепляем
        form_embed = build_info(
            title="📋 Анкета кандидата",
            description=f"Кандидат: {user.mention}\nID: `{user.id}`\n\n"
                        f"Заполнено: {msk_timestamp()}",
            color=embeds.COLOR_MAIN,
        )
        for q, a in answers:
            form_embed.add_field(
                name=q[:256],
                value=a[:1024] if a else "—",
                inline=False,
            )

        try:
            form_msg = await channel.send(embed=form_embed)
            await form_msg.pin()
            # Сохраняем ID закреплённого сообщения анкеты в БД (через form_text)
            # На самом деле, мы уже сохранили form_text выше.
        except discord.HTTPException as e:
            log.warning("Не удалось закрепить анкету: %s", e)

        # 8. Записываем сообщения в БД для транскрипта
        await database.message_add(channel.id, user.id, str(user), form_text)

        # 9. Steam-проверка
        await self._check_steam(interaction, channel, answers)

    # ------------------------------------------------------------------------
    # Steam-проверка
    # ------------------------------------------------------------------------

    async def _check_steam(self, interaction: discord.Interaction,
                           channel: discord.TextChannel,
                           answers: list[tuple[str, str]]):
        config = self.config
        api_key = config.get("steam_api_key", "")

        # Находим ответ, где есть SteamID (по ключевому слову)
        steam_raw = None
        for q, a in answers:
            if any(kw in q.lower() for kw in ("steamid", "steam id", "профиль steam", "steam")):
                steam_raw = a
                break

        if not steam_raw:
            log.info("SteamID не найден в анкете, пропускаем проверку.")
            return

        # Предупреждение о проверке
        progress_msg = None
        try:
            progress_msg = await channel.send(embed=build_info(
                title="🛡️ Проверка Steam-аккаунта",
                description=(
                    "⏳ **Идёт проверка...**\n\n"
                    "`▸` Парсинг SteamID...\n"
                    "`▸` Запрос к Steam API...\n"
                    "`▸` Проверка VAC-банов...\n"
                    "`▸` Подсчёт часов в Rust..."
                ),
            ))
        except discord.HTTPException:
            pass

        try:
            result = await check_steam_account(api_key, steam_raw)
        except Exception as e:
            log.exception("Steam API ошибка: %s", e)
            result = {"success": False, "error": str(e)}

        if not result.get("success"):
            error_msg = result.get("error", "неизвестно")
            # Человеко-читаемые сообщения для частых ошибок
            if "invalid_api_key" in str(error_msg):
                error_msg = "Невалидный Steam API ключ. Обратитесь к разработчику бота."
            elif "rate_limited" in str(error_msg):
                error_msg = "Превышен лимит запросов к Steam API. Попробуйте позже."

            embed = build_warning(
                title="🛡️ Проверка Steam — не удалась",
                description=(
                    f"## ⚠️ Не удалось проверить аккаунт\n\n"
                    f"**Ввод:** `{steam_raw[:200]}`\n"
                    f"**Причина:** `{error_msg}`\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💡 **Совет модератору:** проверьте аккаунт вручную."
                ),
            )
            try:
                if progress_msg:
                    await progress_msg.edit(embed=embed)
                else:
                    await channel.send(embed=embed)
            except discord.HTTPException:
                pass
            return

        # ── Цвет и статус бана ────────────────────────────────────────────────
        if result["vac_banned"] or result["community_banned"]:
            color = COLOR_ERROR
            if result["vac_banned"] and result["community_banned"]:
                status_text = "🔴 VAC + Community бан"
            elif result["vac_banned"]:
                status_text = "🔴 VAC-бан"
            else:
                status_text = "🔴 Community-бан"
            risk_emoji = "⚠️"
        elif result["profile_state"] == "private":
            color = COLOR_WARNING
            status_text = "🟡 Профиль приватный"
            risk_emoji = "⚠️"
        else:
            color = COLOR_SUCCESS
            status_text = "🟢 Аккаунт чистый"
            risk_emoji = "✅"

        # ── Часы в Rust с цветовой индикацией ────────────────────────────────
        source = result.get("source", "unknown")
        if result["hours_rust"] is None:
            if result["profile_state"] == "private":
                hours_text = "🔒 Скрыто (приватный профиль)"
                hours_emoji = "🔒"
            elif source in ("html", "mixed") and not api_key:
                # HTML не может получить playtime без логина Steam
                hours_text = "ℹ️ Требуется Steam API ключ"
                hours_emoji = "ℹ️"
            else:
                hours_text = "❌ Rust не найдена в библиотеке"
                hours_emoji = "❌"
        else:
            hours = result['hours_rust']
            if hours >= 500:
                hours_emoji = "🔥"
            elif hours >= 100:
                hours_emoji = "⚔️"
            elif hours >= 20:
                hours_emoji = "🎮"
            else:
                hours_emoji = "🌱"
            hours_text = f"{hours_emoji} **{hours:.1f}** часов"

        # ── Онлайн-статус ────────────────────────────────────────────────────
        online_status = result.get("online_status", "unknown")
        status_icons = {
            "online": "🟢 Онлайн",
            "offline": "⚫ Офлайн",
            "in-game": "🎮 В игре",
            "unknown": "❓ Неизвестно",
        }
        online_display = status_icons.get(online_status, "❓ Неизвестно")

        # ── Дополнительные поля ───────────────────────────────────────────────
        persona = result.get("persona") or "—"
        profile_url = result.get("profile_url", "—")
        steamid = result.get("steamid", "—")
        last_seen = result.get("last_seen", "—")
        account_created = result.get("account_created", "—")
        country = result.get("country_code") or "—"
        playing_now = result.get("currently_playing")

        # Флаг страны (через эмодзи)
        if country and country != "—" and len(country) == 2:
            # Regional indicator symbols: codepoints 0x1F1E6 + (letter - 'A')
            try:
                flag = chr(0x1F1E6 + ord(country[0]) - ord('A')) + \
                       chr(0x1F1E6 + ord(country[1]) - ord('A'))
                country_display = f"{flag} {country}"
            except Exception:
                country_display = country
        else:
            country_display = country

        # Источник данных
        source_text = {
            "api": "🌐 Steam Web API",
            "html": "📡 HTML-парсинг",
            "mixed": "🌐+📡 API + HTML",
        }.get(source, "❓ Неизвестно")

        # ── Заголовок ─────────────────────────────────────────────────────────
        if result["vac_banned"] or result["community_banned"]:
            title = "🚨 ОБНАРУЖЕН БАН!"
        elif result["profile_state"] == "private":
            title = "🛡️ Профиль приватный"
        elif source == "html":
            title = "🛡️ Базовая проверка Steam"
        else:
            title = "🛡️ Проверка Steam завершена"

        # ── Описание ──────────────────────────────────────────────────────────
        description = (
            f"## {risk_emoji} {persona}\n\n"
            f"Аккаунт кандидата {self.user.mention} проверен.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        # Если HTML-парсинг — добавим предупреждение
        if source == "html" and not api_key:
            description += (
                f"\n\n⚠️ **Базовая проверка** — Steam API ключ не настроен.\n"
                f"Для получения VAC-банов и часов в Rust добавьте ключ в `config.json`.\n"
                f"Получить бесплатно: https://steamcommunity.com/dev/apikey"
            )

        # ── Поля ───────────────────────────────────────────────────────────────
        fields = [
            ("🛡️ Статус аккаунта", status_text, True),
            ("🎮 Часы в Rust", hours_text, True),
            ("📡 Статус", online_display, True),
            ("🌍 Страна", country_display, True),
            ("📅 Создан", account_created, True),
            ("👁️ Последний заход", last_seen, True),
            ("🌐 Источник", source_text, False),
        ]

        if playing_now:
            fields.append(("🎮 Сейчас играет", f"**{playing_now}**", False))

        fields.extend([
            ("🆔 SteamID64", f"`{steamid}`", False),
            ("🔗 Профиль Steam", f"**[Открыть профиль →]({profile_url})**", False),
        ])

        # Дни с последнего бана
        days_ban = result.get("days_since_last_ban")
        if days_ban and days_ban > 0:
            fields.append((
                "⏰ Дней с последнего бана",
                f"**{days_ban}** дн.",
                True,
            ))

        avatar = result.get("avatar")
        embed = build_info(
            color=color,
            title=title,
            description=description,
            fields=fields,
            thumbnail=avatar if avatar else None,
            footer_text="EGODiscord System • Steam Verification",
        )

        # Добавляем author-блок с ником и ссылкой на профиль
        if persona != "—":
            embed.set_author(
                name=persona,
                url=profile_url if profile_url != "—" else None,
                icon_url=avatar if avatar else None,
            )

        try:
            if progress_msg:
                await progress_msg.edit(embed=embed)
            else:
                await channel.send(embed=embed)
        except discord.HTTPException as e:
            log.warning("Не удалось отправить Steam-проверку: %s", e)
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass


# ============================================================================
# View панели тикетов (Dropdown)
# ============================================================================

class TicketPanelView(ui.View):
    """Persistent view с dropdown меню выбора категории."""

    def __init__(self, config: dict):
        super().__init__(timeout=None)
        self.config = config

        select = ui.Select(
            custom_id="ego_ticket_panel_select",
            placeholder="🎫 Выберите категорию...",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(
                    label="Набор в клан",
                    description="Подать заявку на вступление в клан EGO",
                    emoji="🛡️",
                    value="clan",
                ),
                discord.SelectOption(
                    label="Набор в модерацию",
                    description="Подать заявку на должность модератора",
                    emoji="👑",
                    value="mod",
                ),
            ],
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        try:
            ticket_type = interaction.data["values"][0]
        except (KeyError, IndexError):
            return

        user = interaction.user
        guild = interaction.guild

        # 1. Проверка blacklist
        if await database.blacklist_contains(user.id):
            await interaction.response.send_message(
                embed=embeds.error_blacklisted(),
                ephemeral=True,
            )
            return

        # 2. Проверка открытого тикета
        if await database.ticket_exists_for_user(user.id):
            await interaction.response.send_message(
                embed=embeds.error_already_has_ticket(),
                ephemeral=True,
            )
            return

        # 3. Открываем модал
        modal = ApplicationModal(ticket_type, self.config, user)
        try:
            await interaction.response.send_modal(modal)
        except discord.HTTPException as e:
            log.warning("Не удалось открыть модал: %s", e)
            try:
                await interaction.followup.send(
                    embed=build_error(description=f"Не удалось открыть форму: `{e}`"),
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass


# ============================================================================
# Cog
# ============================================================================

class Tickets(commands.Cog):
    """Создание тикетов и панель."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- .setup (только developer_id) ---------------------------------------
    @commands.command(name="setup")
    @commands.guild_only()
    async def setup_cmd(self, ctx: commands.Context):
        """Установить панель тикетов в текущем канале (только для разработчика)."""
        config = getattr(self.bot, "_config", None)
        if config is None:
            # пробуем загрузить напрямую
            import json
            with open("config.json", "r", encoding="utf-8") as f:
                config = json.load(f)
            self.bot._config = config

        if ctx.author.id != config["developer_id"]:
            # Молча игнорируем, чтобы не раскрывать команду
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return

        embed = build_main(
            title="🛡️ СИСТЕМА НАБОРА EGO",
            description=(
                config.get(
                    "ticket_panel_text",
                    "## 🛡️ СИСТЕМА НАБОРА КЛАНА EGO\n\n"
                    "Добро пожаловать в систему подачи заявок клана **EGO**.\n"
                    "Выберите интересующую вас категорию в меню ниже, чтобы начать.\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                )
            ),
            fields=[
                (
                    "🛡️ Набор в клан",
                    "Хочешь стать частью сильнейшего клана?\n"
                    "Подай заявку и докажи, что достоин носить тег **EGO**.",
                    False,
                ),
                (
                    "👑 Набор в модерацию",
                    "Готов поддерживать порядок и помогать клану расти?\n"
                    "Подай заявку на должность модератора.",
                    False,
                ),
                (
                    "📝 Как это работает",
                    "**1.** Выбери категорию в меню ниже\n"
                    "**2.** Заполни анкету (потребуется SteamID)\n"
                    "**3.** Ожидай голосового обзвона\n"
                    "**4.** Получи ответ от рекрутёра",
                    False,
                ),
            ],
            footer_text="EGODiscord System • Выбери категорию в меню ниже ⬇️",
        )

        view = TicketPanelView(config)
        try:
            await ctx.send(embed=embed, view=view)
        except discord.HTTPException as e:
            await ctx.send(embed=build_error(description=f"Ошибка: `{e}`"))
            return

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # --- on_message: записываем сообщения тикета в БД для транскрипта -------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        # Проверяем, что сообщение в тикете
        ticket = await database.ticket_get(message.channel.id)
        if ticket is None:
            return
        try:
            await database.message_add(
                message.channel.id,
                message.author.id,
                str(message.author),
                message.content or "",
            )
            await database.ticket_update_last_message(message.channel.id)
        except Exception as e:
            log.warning("Не удалось записать сообщение тикета: %s", e)


async def setup(bot: commands.Bot):
    # Загружаем конфиг в атрибут бота, чтобы коги могли к нему обращаться
    import json
    with open("config.json", "r", encoding="utf-8") as f:
        bot._config = json.load(f)
    await bot.add_cog(Tickets(bot))
