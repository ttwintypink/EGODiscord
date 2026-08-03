"""
cogs/tickets.py — Создание тикетов: панель, dropdown, модал, Steam-проверка,
автоматическая смена ника кандидата.

Логика:
    .setup            — установка панели (только для developer_id) — ИНТЕРАКТИВНОЕ МЕНЮ
    Dropdown панели   — выбор «клан» / «модерация»
    Цепочка модалок   — поддержка до 15 вопросов (по 5 полей в каждой модалке)
    Каждый вопрос имеет:
        - title       — название вопроса
        - subtitle    — подвопросник (placeholder)
        - max_length  — максимальная длина ответа
        - min_length  — минимальная длина ответа
        - multiline   — многострочное поле
        - required    — обязательный ли
    Создание канала   — изоляция прав, пинг ролей, ОДНО компактное сообщение управления
    Steam-проверка    — автоматический запрос к Steam API
    Автоник           — "Steam Name | Real Name" при создании тикета
"""
from __future__ import annotations

import logging
from typing import Optional, Any

import discord
from discord import ui, AllowedMentions
from discord.ext import commands

import database
from utils import embeds
from utils.embeds import (
    build_main, build_success, build_error, build_warning, build_info,
    msk_timestamp, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, COLOR_MAIN,
)
from utils.steam_api import check_steam_account

log = logging.getLogger(__name__)


# ============================================================================
# Нормализация вопросов (старый формат строк → новый формат объектов)
# ============================================================================

def _normalize_question(q: Any) -> dict:
    """Приводит вопрос к единому формату dict.

    Принимает:
        - строку (старый формат) → конвертирует
        - dict (новый формат) → дополняет недостающие поля
    """
    if isinstance(q, str):
        return {
            "title": q[:45],
            "subtitle": "",
            "max_length": 500,
            "min_length": 1,
            "multiline": len(q) > 30,
            "required": True,
            "is_real_name": False,
            "is_steam": "steam" in q.lower(),
        }
    if isinstance(q, dict):
        return {
            "title": str(q.get("title", "Вопрос"))[:45],
            "subtitle": str(q.get("subtitle", ""))[:100],
            "max_length": min(int(q.get("max_length", 500)), 4000),
            "min_length": max(int(q.get("min_length", 0)), 0),
            "multiline": bool(q.get("multiline", False)),
            "required": bool(q.get("required", True)),
            "is_real_name": bool(q.get("is_real_name", False)),
            "is_steam": bool(q.get("is_steam", False)),
        }
    return {
        "title": "Вопрос",
        "subtitle": "",
        "max_length": 500,
        "min_length": 1,
        "multiline": False,
        "required": True,
        "is_real_name": False,
        "is_steam": False,
    }


def _normalize_questions(questions: list) -> list[dict]:
    """Нормализует список вопросов."""
    if not isinstance(questions, list):
        return []
    return [_normalize_question(q) for q in questions if q]


def _capitalize_first(text: Optional[str]) -> str:
    """Делает первую букву строки заглавной.

    Корректно работает с кириллицей и латиницей:
        'имя'   → 'Имя'
        'ummi'  → 'Ummi'
        'александр' → 'Александр'
        'john doe'  → 'John doe'  (только первая буква строки)

    Остальные буквы не изменяются — кандидат мог написать 'macDonald'
    и это останется 'MacDonald', а не 'Macdonald'.
    """
    if not text:
        return ""
    text = str(text).strip()
    if not text:
        return ""
    # str.upper() корректно работает с Unicode (включая кириллицу)
    return text[0].upper() + text[1:]


# ============================================================================
# Премиум-фабрика embed-а панели тикетов
# ============================================================================

def _build_panel_embed(config: dict) -> discord.Embed:
    """Собирает embed панели тикетов с премиум-дизайном."""
    custom_text = config.get("ticket_panel_text", "")
    if custom_text and len(custom_text) > 20:
        description = custom_text
    else:
        description = (
            "## 🛡️ СИСТЕМА НАБОРА КЛАНА EGO\n\n"
            "Добро пожаловать в **официальную систему набора** клана **EGO** — "
            "одного из самых активных и сильных кланов Rust.\n\n"
            "Выберите интересующую вас категорию в меню ниже, чтобы подать заявку. "
            "Мы рассматриваем каждую анкету индивидуально.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    embed = discord.Embed(
        title="🛡️ СИСТЕМА НАБОРА EGO",
        description=description,
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )
    embed.add_field(
        name="🛡️ Набор в клан",
        value=(
            "Хочешь стать частью сильнейшего клана?\n"
            "Подай заявку и докажи, что достоин носить тег **EGO**."
        ),
        inline=False,
    )
    embed.add_field(
        name="👑 Набор в модерацию",
        value=(
            "Готов поддерживать порядок и помогать клану расти?\n"
            "Подай заявку на должность модератора."
        ),
        inline=False,
    )
    embed.add_field(
        name="📝 Как это работает",
        value=(
            "**1.** Выбери категорию в меню ниже\n"
            "**2.** Заполни анкету (потребуется SteamID или ссылка на профиль)\n"
            "**3.** Ожидай собеседования от рекрутёра\n"
            "**4.** Получи ответ от рекрутёра в личные сообщения"
        ),
        inline=False,
    )
    embed.set_thumbnail(
        url="https://cdn.discordapp.com/embed/avatars/0.png"
    )
    embed.set_footer(text="EGODiscord System • Выбери категорию в меню ниже ⬇️")
    return embed


# ============================================================================
# Цепочка модалок (поддержка >5 вопросов)
# ============================================================================

class ApplicationModal(ui.Modal):
    """
    Модалка анкеты. Поддерживает цепочку: если вопросов >5, после отправки
    первой модалки открывается следующая с оставшимися вопросами.

    Ответы накапливаются в self._all_answers.
    """

    def __init__(self, ticket_type: str, config: dict, user: discord.Member,
                 all_answers: list[tuple[dict, str]] = None,
                 chunk_index: int = 0):
        self.ticket_type = ticket_type
        self.config = config
        self.user = user
        self._all_answers = all_answers or []
        self._chunk_index = chunk_index

        questions_key = "questions_clan" if ticket_type == "clan" else "questions_mod"
        raw_questions = config.get(questions_key, [])
        all_questions = _normalize_questions(raw_questions)[:15]  # максимум 15 вопросов
        if not all_questions:
            all_questions = [_normalize_question("Ваш SteamID или ссылка на профиль Steam?")]

        # Делим на чанки по 5
        self._all_questions = all_questions
        chunk_size = 5
        chunks = [all_questions[i:i + chunk_size]
                  for i in range(0, len(all_questions), chunk_size)]
        self._chunks = chunks
        self._current_chunk = chunks[chunk_index] if chunk_index < len(chunks) else []

        total = len(all_questions)
        start_num = chunk_index * chunk_size + 1
        end_num = start_num + len(self._current_chunk) - 1

        is_clan = ticket_type == "clan"
        if len(chunks) > 1:
            title = f"{'🛡️ Клан' if is_clan else '👑 Модер'} {chunk_index + 1}/{len(chunks)} ({start_num}-{end_num} из {total})"
        else:
            title = f"{'🛡️ Заявка в клан' if is_clan else '👑 Заявка в модерацию'}"
        super().__init__(title=title[:45])

        self._inputs: list[tuple[dict, ui.TextInput]] = []
        for i, q in enumerate(self._current_chunk):
            num = start_num + i
            label = f"{num}. {q['title']}"[:45]
            placeholder = q.get("subtitle", "")[:100] or None
            inp = ui.TextInput(
                label=label,
                placeholder=placeholder,
                required=q.get("required", True),
                style=discord.TextStyle.paragraph if q.get("multiline") else discord.TextStyle.short,
                max_length=q.get("max_length", 500),
                min_length=q.get("min_length", 0) if q.get("required", True) else 0,
            )
            self._inputs.append((q, inp))
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        # Собираем ответы из текущего чанка
        for q, inp in self._inputs:
            value = inp.value.strip() if inp.value else ""
            self._all_answers.append((q, value))

        # Если есть ещё чанки — открываем следующую модалку
        # В discord.py 2.x: после отправки модалки interaction.response уже использован,
        # но on_submit получает НОВЫЙ interaction, для которого можно вызвать send_modal
        next_idx = self._chunk_index + 1
        if next_idx < len(self._chunks):
            next_modal = ApplicationModal(
                self.ticket_type, self.config, self.user,
                self._all_answers, next_idx,
            )
            try:
                await interaction.response.send_modal(next_modal)
            except discord.HTTPException as e:
                log.warning("Не удалось открыть следующую модалку: %s", e)
                # Fallback: уведомить пользователя
                try:
                    await interaction.response.send_message(
                        embed=build_error(
                            description=f"Не удалось открыть следующую часть анкеты: `{e}`. "
                                        f"Попробуйте ещё раз.",
                        ),
                        ephemeral=True,
                    )
                except discord.HTTPException:
                    pass
                return
            return

        # Это последний чанк — собираем все ответы
        try:
            await interaction.response.send_message(
                embed=build_success(
                    title="✅ Анкета отправлена",
                    description="Создаю канал тикета, подождите пару секунд...",
                ),
                ephemeral=True,
            )
        except discord.HTTPException:
            pass

        # Создаём тикет
        await self._create_ticket(interaction, self._all_answers)

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
    # Создание канала тикета — КОМПАКТНО
    # ------------------------------------------------------------------------

    async def _create_ticket(self, interaction: discord.Interaction,
                             answers: list[tuple[dict, str]]):
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
                manage_nicknames=True,
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

        # 5. Сохраняем в БД (с исходным ником)
        form_text_parts = []
        for q, a in answers:
            form_text_parts.append(f"**{q['title']}**\n{a}")
        form_text = "\n".join(form_text_parts)

        original_nick = user.display_name  # исходный ник (до изменения)
        await database.ticket_create(
            channel.id, user.id, self.ticket_type, form_text,
            original_nickname=original_nick,
        )

        # 6. Сохраняем ID сообщения управления в БД (через topic или отдельный механизм)
        # ИЩЕМ настоящее имя для ника
        real_name = None
        steam_raw = None
        for q, a in answers:
            if q.get("is_real_name"):
                real_name = a
            if q.get("is_steam"):
                steam_raw = a
            elif not steam_raw and "steam" in q.get("title", "").lower():
                steam_raw = a

        # 7. Пинг ролей
        ping_role_ids = (config.get("ping_roles_clan", []) if self.ticket_type == "clan"
                         else config.get("ping_roles_mod", []))
        ping_str = " ".join(f"<@&{rid}>" for rid in ping_role_ids) if ping_role_ids else ""
        content_parts = [f"{user.mention}", ping_str] if ping_str else [f"{user.mention}"]
        content = " ".join(p for p in content_parts if p)

        # 8. Создаём ОДНО компактное сообщение управления
        # Это сообщение будет редактироваться при claim/call/close — без спама embed'ами
        from cogs.ticket_control import build_control_embed, TicketControlView
        control_embed = build_control_embed(
            user=user,
            ticket_type=self.ticket_type,
            status="open",
            claimer=None,
            voice_channel=None,
            steam_status="pending",
        )
        control_view = TicketControlView(config)

        try:
            control_msg = await channel.send(
                content=content,
                embed=control_embed,
                view=control_view,
                allowed_mentions=AllowedMentions(
                    users=True, roles=True, everyone=False
                ),
            )
            await control_msg.pin()
        except discord.HTTPException as e:
            log.warning("Не удалось отправить/закрепить управление: %s", e)
            control_msg = None

        # 9. Анкета — отдельный embed (закреплённый)
        form_embed = discord.Embed(
            title="📋 Анкета кандидата",
            description=(
                f"## 👤 {user.mention}\n"
                f"**ID:** `{user.id}`\n"
                f"**Заполнено:** {msk_timestamp()}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=COLOR_MAIN,
            timestamp=embeds.now_msk(),
        )
        form_embed.set_thumbnail(url=user.display_avatar.url)
        form_embed.set_footer(text="EGODiscord System • Анкета кандидата")

        for i, (q, a) in enumerate(answers, 1):
            form_embed.add_field(
                name=f"❓ {i}. {q['title'][:250]}",
                value=(a[:1024] if a else "—"),
                inline=False,
            )

        try:
            form_msg = await channel.send(embed=form_embed)
            await form_msg.pin()
        except discord.HTTPException as e:
            log.warning("Не удалось закрепить анкету: %s", e)
            form_msg = None

        # 10. Записываем сообщения в БД для транскрипта
        await database.message_add(channel.id, user.id, str(user), form_text)

        # 11. Steam-проверка + автоник (компактно — отдельное сообщение,
        # которое редактируется: прогресс → результат)
        await self._check_steam_and_set_nick(
            interaction, channel, control_msg, answers, real_name, steam_raw
        )

    # ------------------------------------------------------------------------
    # Steam-проверка + автоник — ОДНО сообщение (редактируется)
    # ------------------------------------------------------------------------

    async def _check_steam_and_set_nick(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        control_msg: Optional[discord.Message],
        answers: list[tuple[dict, str]],
        real_name: Optional[str],
        steam_raw: Optional[str],
    ):
        config = self.config
        api_key = config.get("steam_api_key", "")

        # Если нет SteamID — пропускаем
        if not steam_raw:
            # Меняем ник на "Real Name" (если есть) — без Steam
            if real_name and self.user:
                await self._set_nickname(self.user, real_name, None)
            return

        # Создаём сообщение с прогрессом
        progress_msg = None
        try:
            progress_msg = await channel.send(embed=discord.Embed(
                title="🛡️ Проверка Steam-аккаунта",
                description=(
                    "## ⏳ Идёт проверка...\n\n"
                    "```\n"
                    "▸ Парсинг SteamID ........... ⏳\n"
                    "▸ Запрос к Steam API ........ ожидание\n"
                    "▸ Проверка VAC-банов ........ ожидание\n"
                    "▸ Подсчёт часов в Rust ...... ожидание\n"
                    "```\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⏱️ Обычно занимает 3–8 секунд."
                ),
                color=COLOR_MAIN,
                timestamp=embeds.now_msk(),
            ).set_footer(text="EGODiscord System • Steam Verification"))
        except discord.HTTPException:
            pass

        # Запрос к Steam API
        try:
            result = await check_steam_account(api_key, steam_raw)
        except Exception as e:
            log.exception("Steam API ошибка: %s", e)
            result = {"success": False, "error": str(e)}

        if not result.get("success"):
            error_msg = result.get("error", "неизвестно")
            if "invalid_api_key" in str(error_msg):
                error_msg = (
                    "Невалидный Steam API ключ. Обратитесь к разработчику бота "
                    "или используйте `.editor` → 🔑 Steam API ключ."
                )
            elif "rate_limited" in str(error_msg):
                error_msg = "Превышен лимит запросов к Steam API. Попробуйте через минуту."
            elif "timeout" in str(error_msg) or "network" in str(error_msg):
                error_msg = (
                    "Steam временно недоступен (таймаут/сеть). "
                    "Проверьте аккаунт вручную по ссылке."
                )
            elif "server_error" in str(error_msg):
                error_msg = (
                    "Steam сервер вернул ошибку. Попробуйте ещё раз через минуту "
                    "или проверьте аккаунт вручную."
                )

            embed = discord.Embed(
                title="🛡️ Проверка Steam — не удалась",
                description=(
                    f"## ⚠️ Не удалось проверить аккаунт\n\n"
                    f"**Ввод:** `{steam_raw[:200]}`\n"
                    f"**Причина:** {error_msg}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💡 **Совет модератору:** проверьте аккаунт вручную."
                ),
                color=COLOR_WARNING,
                timestamp=embeds.now_msk(),
            )
            embed.set_footer(text="EGODiscord System • Steam Verification")
            try:
                if progress_msg:
                    await progress_msg.edit(embed=embed)
                else:
                    await channel.send(embed=embed)
            except discord.HTTPException:
                pass
            return

        # ── Успешный результат — цвет и статус бана ──────────────────────────
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

        # ── Часы в Rust ──────────────────────────────────────────────────────
        source = result.get("source", "unknown")
        if result["hours_rust"] is None:
            if result["profile_state"] == "private":
                hours_text = "🔒 Скрыто (приватный профиль)"
                hours_emoji = "🔒"
            elif source in ("html", "mixed") and not api_key:
                hours_text = "ℹ️ Требуется Steam API ключ"
                hours_emoji = "ℹ️"
            else:
                hours_text = "❌ Rust не найдена в библиотеке"
                hours_emoji = "❌"
        else:
            hours = result['hours_rust']
            if hours >= 1000:
                hours_emoji = "🔥"
            elif hours >= 500:
                hours_emoji = "⚔️"
            elif hours >= 100:
                hours_emoji = "🎮"
            elif hours >= 20:
                hours_emoji = "🌱"
            else:
                hours_emoji = "🆕"
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

        if country and country != "—" and len(country) == 2:
            try:
                flag = chr(0x1F1E6 + ord(country[0]) - ord('A')) + \
                       chr(0x1F1E6 + ord(country[1]) - ord('A'))
                country_display = f"{flag} {country}"
            except Exception:
                country_display = country
        else:
            country_display = country

        source_text = {
            "api": "🌐 Steam Web API",
            "html": "📡 HTML-парсинг",
            "mixed": "🌐+📡 API + HTML",
        }.get(source, "❓ Неизвестно")

        if result["vac_banned"] or result["community_banned"]:
            title = "🚨 ОБНАРУЖЕН БАН!"
        elif result["profile_state"] == "private":
            title = "🛡️ Профиль приватный"
        elif source == "html":
            title = "🛡️ Базовая проверка Steam"
        else:
            title = "🛡️ Проверка Steam завершена"

        description = (
            f"## {risk_emoji} {persona}\n\n"
            f"Аккаунт кандидата {self.user.mention} проверен.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        if source == "html" and not api_key:
            description += (
                f"\n\n⚠️ **Базовая проверка** — Steam API ключ не настроен.\n"
                f"Для VAC-банов и часов в Rust добавьте ключ через "
                f"`.editor` → 🔑 Steam API ключ."
            )

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=embeds.now_msk(),
        )
        embed.add_field(name="🛡️ Статус аккаунта", value=status_text, inline=True)
        embed.add_field(name="🎮 Часы в Rust", value=hours_text, inline=True)
        embed.add_field(name="📡 Статус", value=online_display, inline=True)
        embed.add_field(name="🌍 Страна", value=country_display, inline=True)
        embed.add_field(name="📅 Создан", value=account_created, inline=True)
        embed.add_field(name="👁️ Последний заход", value=last_seen, inline=True)
        embed.add_field(name="🌐 Источник", value=source_text, inline=False)

        if playing_now:
            embed.add_field(name="🎮 Сейчас играет", value=f"**{playing_now}**", inline=False)

        embed.add_field(
            name="🆔 SteamID64",
            value=f"```\n{steamid}\n```",
            inline=False,
        )
        embed.add_field(
            name="🔗 Профиль Steam",
            value=f"**[Открыть профиль →]({profile_url})**",
            inline=False,
        )

        days_ban = result.get("days_since_last_ban")
        if days_ban and days_ban > 0:
            embed.add_field(
                name="⏰ Дней с последнего бана",
                value=f"**{days_ban}** дн.",
                inline=True,
            )

        avatar = result.get("avatar")
        if avatar:
            embed.set_thumbnail(url=avatar)

        if persona != "—":
            embed.set_author(
                name=persona,
                url=profile_url if profile_url != "—" else None,
                icon_url=avatar if avatar else None,
            )

        embed.set_footer(text="EGODiscord System • Steam Verification")

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

        # ── АВТОНИК: меняем ник кандидата на "Steam Name | Real Name" ─────────
        await self._set_nickname(self.user, real_name, persona if persona != "—" else None)

    async def _set_nickname(self, member: discord.Member,
                            real_name: Optional[str],
                            steam_name: Optional[str]):
        """Меняет ник кандидата на формат 'Steam Name | Real Name'.

        Обе части автоматически пишутся с большой буквы — даже если кандидат
        ввёл всё маленькими. Работает для кириллицы ('имя' → 'Имя') и латиницы
        ('ummi' → 'Ummi').
        """
        if not member or not (real_name or steam_name):
            return

        # Формируем новый ник — каждая часть с большой буквы
        parts = []
        if steam_name:
            parts.append(_capitalize_first(steam_name)[:30])
        if real_name:
            parts.append(_capitalize_first(real_name)[:20])
        if not parts:
            return

        new_nick = " | ".join(parts)[:32]  # лимит Discord 32 символа

        try:
            await member.edit(nick=new_nick, reason="Автоник: заявка создана")
            log.info("Ник изменён: %s → %s", member, new_nick)
        except discord.Forbidden:
            log.warning("Нет прав на смену ника для %s", member)
        except discord.HTTPException as e:
            log.warning("Не удалось изменить ник: %s", e)


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

        # 3. Открываем первую модалку
        modal = ApplicationModal(ticket_type, self.config, user, [], 0)
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
# ИНТЕРАКТИВНОЕ МЕНЮ .setup (вместо кучи кнопок)
# ============================================================================

class SetupDropdownView(ui.View):
    """Меню .setup с dropdown вместо кучи кнопок."""

    def __init__(self, config: dict, owner_id: int, bot: commands.Bot):
        super().__init__(timeout=300)
        self.config = config
        self.owner_id = owner_id
        self.bot = bot
        self.message: Optional[discord.Message] = None

        # Создаём dropdown с действиями
        select = ui.Select(
            placeholder="⚡ Выберите действие настройки...",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(
                    label="Создать панель тикетов",
                    description="Установить новую панель в этом канале",
                    emoji="🎫",
                    value="create_panel",
                ),
                discord.SelectOption(
                    label="Пересоздать панель",
                    description="Удалить старую панель и создать новую",
                    emoji="♻️",
                    value="recreate_panel",
                ),
                discord.SelectOption(
                    label="Открыть редактор настроек",
                    description="Настройка вопросов, ролей, ключей, цвета",
                    emoji="🛠️",
                    value="editor",
                ),
                discord.SelectOption(
                    label="Превью панели",
                    description="Посмотреть как выглядит панель сейчас",
                    emoji="👁",
                    value="preview",
                ),
                discord.SelectOption(
                    label="Открыть справку",
                    description="Показать список всех команд",
                    emoji="📖",
                    value="help",
                ),
                discord.SelectOption(
                    label="Информация о боте",
                    description="Версия, аптайм, сервера",
                    emoji="ℹ️",
                    value="sysinfo",
                ),
                discord.SelectOption(
                    label="Закрыть меню",
                    description="Удалить это сообщение",
                    emoji="✖️",
                    value="close",
                ),
            ],
        )
        select.callback = self.on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=build_error(
                    description="Это меню настройки открыл другой администратор. "
                                "Используйте `.setup`, чтобы открыть своё."
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

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]

        if value == "create_panel":
            await self._action_create_panel(interaction)
        elif value == "recreate_panel":
            await self._action_recreate_panel(interaction)
        elif value == "editor":
            await self._action_open_editor(interaction)
        elif value == "preview":
            await self._action_preview(interaction)
        elif value == "help":
            await self._action_help(interaction)
        elif value == "sysinfo":
            await self._action_sysinfo(interaction)
        elif value == "close":
            try:
                await interaction.response.defer()
                if self.message:
                    await self.message.delete()
            except discord.HTTPException:
                pass

    async def _action_create_panel(self, interaction: discord.Interaction):
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

    async def _action_recreate_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        old_panel = None
        try:
            async for msg in channel.history(limit=50, oldest_first=False):
                if msg.author == interaction.guild.me and msg.components:
                    for row in msg.components:
                        for comp in getattr(row, "children", []):
                            if isinstance(comp, discord.SelectMenu) and \
                                    comp.custom_id == "ego_ticket_panel_select":
                                old_panel = msg
                                break
                        if old_panel:
                            break
                if old_panel:
                    break
        except discord.HTTPException:
            pass

        if old_panel:
            try:
                await old_panel.delete()
            except discord.HTTPException:
                pass

        embed = _build_panel_embed(self.config)
        view = TicketPanelView(self.config)
        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=build_error(description=f"Не удалось создать панель: `{e}`"),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=build_success(
                title="✅ Панель пересоздана",
                description=f"Старая панель удалена, новая создана в {channel.mention}.",
            ),
            ephemeral=True,
        )

    async def _action_open_editor(self, interaction: discord.Interaction):
        from cogs.editor import EditorDashboardView, _dashboard_embed
        view = EditorDashboardView(self.config, interaction.user.id)
        msg = await interaction.channel.send(
            embed=_dashboard_embed(self.config), view=view
        )
        view.message = msg
        await interaction.response.send_message(
            embed=build_success(description=f"Редактор открыт в {interaction.channel.mention}."),
            ephemeral=True,
        )

    async def _action_preview(self, interaction: discord.Interaction):
        embed = _build_panel_embed(self.config)
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    async def _action_help(self, interaction: discord.Interaction):
        from cogs.help import _build_main_help, HelpView
        embed = _build_main_help(interaction.user, self.config, self.bot)
        help_view = HelpView(interaction.user, self.config, interaction.user.id, self.bot)
        for opt in help_view.select.options:
            opt.default = (opt.value == "home")
        await interaction.channel.send(embed=embed, view=help_view)
        try:
            msg = await interaction.channel.fetch_message(interaction.channel.last_message_id)
            help_view.message = msg
        except discord.HTTPException:
            pass
        await interaction.response.send_message(
            embed=build_success(description="Справка открыта ниже."),
            ephemeral=True,
        )

    async def _action_sysinfo(self, interaction: discord.Interaction):
        from cogs.menu import _build_system_info_embed
        embed = _build_system_info_embed(self.bot)
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _build_setup_menu_embed() -> discord.Embed:
    """Embed меню .setup."""
    return discord.Embed(
        title="🛠️ Меню настройки EGO",
        description=(
            "## ⚙️ Панель управления ботом\n\n"
            "Выберите действие в **выпадающем списке** ниже.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "### 📋 Доступные действия\n"
            "🎫 **Создать панель** — установить панель тикетов\n"
            "♻️ **Пересоздать панель** — обновить с удалением старой\n"
            "🛠️ **Открыть редактор** — настройки бота (вопросы, роли, ключ)\n"
            "👁 **Превью панели** — посмотреть как выглядит панель\n"
            "📖 **Справка** — список всех команд\n"
            "ℹ️ **Информация о боте** — версия, аптайм, сервера\n"
            "✖️ **Закрыть меню** — убрать это сообщение\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    ).set_footer(text="EGODiscord System • .setup • Меню настройки")


# ============================================================================
# Cog
# ============================================================================

class Tickets(commands.Cog):
    """Создание тикетов и панель."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- .setup (только developer_id) — ИНТЕРАКТИВНОЕ МЕНЮ -------------------
    @commands.command(name="setup")
    @commands.guild_only()
    async def setup_cmd(self, ctx: commands.Context):
        """Открыть интерактивное меню настройки бота (только для разработчика)."""
        config = getattr(self.bot, "_config", None)
        if config is None:
            import json
            with open("config.json", "r", encoding="utf-8") as f:
                config = json.load(f)
            self.bot._config = config

        if ctx.author.id != config["developer_id"]:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return

        view = SetupDropdownView(config, ctx.author.id, self.bot)
        msg = await ctx.send(embed=_build_setup_menu_embed(), view=view)
        view.message = msg

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # --- on_message: записываем сообщения тикета в БД для транскрипта -------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
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
    import json
    with open("config.json", "r", encoding="utf-8") as f:
        bot._config = json.load(f)
    await bot.add_cog(Tickets(bot))
