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
    COLOR_PIRATE,
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
        def _safe_int(v, default: int, lo: int = None, hi: int = None) -> int:
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = default
            if lo is not None:
                v = max(lo, v)
            if hi is not None:
                v = min(hi, v)
            return v
        return {
            "title": str(q.get("title", "Вопрос"))[:45],
            "subtitle": str(q.get("subtitle", ""))[:100],
            "max_length": _safe_int(q.get("max_length", 500), 500, 1, 4000),
            "min_length": _safe_int(q.get("min_length", 0), 0, 0, 4000),
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
    """Собирает embed панели тикетов с премиум-дизайном.

    Все текстовые элементы читаются из config (с fallback на дефолтные
    значения), чтобы их можно было редактировать через `.editor`.
    """
    # ── Дефолтные значения (используются если в config ничего нет) ──────────
    DEFAULT_PANEL_TITLE       = "🛡️ СИСТЕМА НАБОРА EGO"
    DEFAULT_PANEL_DESC        = (
        "## 🛡️ СИСТЕМА НАБОРА КЛАНА EGO\n\n"
        "Добро пожаловать в **официальную систему набора** клана **EGO** — "
        "одного из самых активных и сильных кланов Rust.\n\n"
        "Выберите интересующую вас категорию в меню ниже, чтобы подать заявку. "
        "Мы рассматриваем каждую анкету индивидуально.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    DEFAULT_CLAN_FIELD_TITLE  = "🛡️ Набор в клан"
    DEFAULT_CLAN_FIELD_DESC   = (
        "Хочешь стать частью сильнейшего клана?\n"
        "Подай заявку и докажи, что достоин носить тег **EGO**."
    )
    DEFAULT_MOD_FIELD_TITLE   = "👑 Набор в модерацию"
    DEFAULT_MOD_FIELD_DESC    = (
        "Готов поддерживать порядок и помогать клану расти?\n"
        "Подай заявку на должность модератора."
    )
    DEFAULT_HOWTO_FIELD_TITLE = "📝 Как это работает"
    DEFAULT_HOWTO_FIELD_DESC  = (
        "**1.** Выбери категорию в меню ниже\n"
        "**2.** Заполни анкету (потребуется SteamID или ссылка на профиль)\n"
        "**3.** Ожидай собеседования от рекрутёра\n"
        "**4.** Получи ответ от рекрутёра в личные сообщения"
    )
    DEFAULT_PANEL_FOOTER      = "EGODiscord System • Выбери категорию в меню ниже ⬇️"
    DEFAULT_PANEL_THUMBNAIL   = "https://cdn.discordapp.com/embed/avatars/0.png"

    # ── Читаем значения из config ───────────────────────────────────────────
    # ticket_panel_text — это ОПИСАНИЕ панели (старое поле, оставляем для
    # обратной совместимости). Если задан panel_description, он имеет приоритет.
    custom_text = config.get("ticket_panel_text", "")
    description = config.get("panel_description") or (
        custom_text if custom_text and len(custom_text) > 20 else DEFAULT_PANEL_DESC
    )

    title = config.get("panel_title") or DEFAULT_PANEL_TITLE
    clan_field_title = config.get("panel_clan_field_title") or DEFAULT_CLAN_FIELD_TITLE
    clan_field_desc  = config.get("panel_clan_field_desc")  or DEFAULT_CLAN_FIELD_DESC
    mod_field_title  = config.get("panel_mod_field_title")  or DEFAULT_MOD_FIELD_TITLE
    mod_field_desc   = config.get("panel_mod_field_desc")   or DEFAULT_MOD_FIELD_DESC
    howto_field_title = config.get("panel_howto_field_title") or DEFAULT_HOWTO_FIELD_TITLE
    howto_field_desc  = config.get("panel_howto_field_desc")  or DEFAULT_HOWTO_FIELD_DESC
    footer_text = config.get("panel_footer") or DEFAULT_PANEL_FOOTER
    thumbnail_url = config.get("panel_thumbnail_url") or config.get("brand_thumbnail_url") or DEFAULT_PANEL_THUMBNAIL

    # ── Цвет embed ──────────────────────────────────────────────────────────
    embed_color_str = config.get("embed_color", "5865F2")
    try:
        embed_color_int = int(embed_color_str, 16)
    except (ValueError, TypeError):
        embed_color_int = COLOR_MAIN

    embed = discord.Embed(
        title=title[:256],
        description=description[:4096],
        color=embed_color_int,
        timestamp=embeds.now_msk(),
    )
    embed.add_field(name=clan_field_title[:256], value=clan_field_desc[:1024], inline=False)
    embed.add_field(name=mod_field_title[:256],  value=mod_field_desc[:1024],  inline=False)
    embed.add_field(name=howto_field_title[:256], value=howto_field_desc[:1024], inline=False)
    embed.set_thumbnail(url=thumbnail_url)
    embed.set_footer(text=footer_text[:2048])
    return embed


# ── Дефолты для опций dropdown'а панели ─────────────────────────────────────
DEFAULT_PANEL_SELECT_PLACEHOLDER = "🎫 Выберите категорию..."
DEFAULT_CLAN_OPTION_EMOJI  = "🛡️"
DEFAULT_CLAN_OPTION_LABEL  = "Набор в клан"
DEFAULT_CLAN_OPTION_DESC   = "Подать заявку на вступление в клан EGO"
DEFAULT_MOD_OPTION_EMOJI   = "👑"
DEFAULT_MOD_OPTION_LABEL   = "Набор в модерацию"
DEFAULT_MOD_OPTION_DESC    = "Подать заявку на должность модератора"


def _safe_emoji(s: str) -> Optional[str]:
    """Возвращает emoji-строку для discord.SelectOption, либо None.

    Discord принимает:
    - 1 code point (например «🛡»)
    - 2 code points если второй — VS16 (U+FE0F), ZWJ (U+200D) или skin-tone
    - кастомный emoji в формате <:name:id> или <a:name:id>

    Мы принимаем строку длиной 1-30 символов, отбрасываем пробелы и слишком
    длинные строки (>30 — точно не emoji).
    """
    if not s:
        return None
    s = s.strip()
    if not s or len(s) > 30:
        return None
    return s


def _get_panel_select_options(config: dict) -> tuple[str, list[discord.SelectOption]]:
    """Возвращает (placeholder, [SelectOption для клана, SelectOption для модера]).
    Все значения читаются из config с fallback на дефолты — редактируется через
    `.editor` → Внешний вид панели.
    """
    placeholder = config.get("panel_select_placeholder") or DEFAULT_PANEL_SELECT_PLACEHOLDER

    clan_emoji = _safe_emoji(config.get("panel_clan_option_emoji")) or DEFAULT_CLAN_OPTION_EMOJI
    clan_label = config.get("panel_clan_option_label") or DEFAULT_CLAN_OPTION_LABEL
    clan_desc  = config.get("panel_clan_option_desc")  or DEFAULT_CLAN_OPTION_DESC

    mod_emoji = _safe_emoji(config.get("panel_mod_option_emoji")) or DEFAULT_MOD_OPTION_EMOJI
    mod_label = config.get("panel_mod_option_label") or DEFAULT_MOD_OPTION_LABEL
    mod_desc  = config.get("panel_mod_option_desc")  or DEFAULT_MOD_OPTION_DESC

    options = [
        discord.SelectOption(
            label=clan_label[:100],
            description=clan_desc[:100],
            emoji=clan_emoji,
            value="clan",
        ),
        discord.SelectOption(
            label=mod_label[:100],
            description=mod_desc[:100],
            emoji=mod_emoji,
            value="mod",
        ),
    ]
    return placeholder, options


# ============================================================================
# Цепочка модалок (поддержка >5 вопросов)
# ============================================================================

class NextChunkView(ui.View):
    """View с кнопкой "Продолжить" для открытия следующего чанка анкеты.

    Решает проблему: после on_submit модалки interaction.response уже
    использован, и прямой send_modal() падает с ошибкой
    "Value must be one of {4, 5, 6, 7, 10, 12}".

    Паттерн: on_submit → send_message(ephemeral) с этой кнопкой →
    по клику на кнопку открывается следующая модалка (новый interaction,
    response свободен).
    """

    def __init__(self, ticket_type: str, config: dict, user: discord.Member,
                 all_answers: list, next_chunk: int, total_chunks: int):
        super().__init__(timeout=300)  # 5 минут на нажатие кнопки
        self.ticket_type = ticket_type
        self.config = config
        self.user = user
        self.all_answers = all_answers
        self.next_chunk = next_chunk
        self.total_chunks = total_chunks
        self.message: Optional[discord.Message] = None  # для on_timeout

    @ui.button(label="Продолжить заполнение →", emoji="➡️",
               style=discord.ButtonStyle.success, custom_id="ego_next_chunk")
    async def continue_btn(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем, что нажал тот же пользователь, который заполнял анкету
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                embed=build_error(
                    description="Эту анкету заполняет другой пользователь. "
                                "Создайте свою через панель тикетов."
                ),
                ephemeral=True,
            )
            return
        modal = ApplicationModal(
            self.ticket_type, self.config, self.user,
            self.all_answers, self.next_chunk,
        )
        try:
            await interaction.response.send_modal(modal)
        except discord.HTTPException as e:
            log.error("Не удалось открыть следующий чанк через кнопку: %s", e)
            try:
                await interaction.response.send_message(
                    embed=build_error(
                        description=f"Не удалось открыть следующую часть: `{e}`. "
                                    f"Попробуйте ещё раз или обратитесь к администратору."
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        # По таймауту — дизейблим кнопку и обновляем сообщение
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    embed=build_warning(
                        title="⏰ Время истекло",
                        description=(
                            "Время на продолжение анкеты вышло.\n"
                            "Создайте новую заявку через панель тикетов."
                        ),
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass


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

        # Если есть ещё чанки — открываем следующую модалку.
        # ⚠️ ВАЖНО: в discord.py 2.7+ и новом Discord API прямой вызов
        # interaction.response.send_modal() из on_submit() часто падает с
        # ошибкой "Value must be one of {4, 5, 6, 7, 10, 12}" (Invalid Form Body).
        # Это происходит потому, что interaction.response уже "потрачен" на
        # submit, и send_modal пытается отправить новый компонент поверх.
        #
        # Решение: сначала отправляем ephemeral-сообщение с кнопкой
        # "Продолжить", по клику на которую открывается следующая модалка.
        # Это надёжно работает во всех версиях Discord API.
        next_idx = self._chunk_index + 1
        if next_idx < len(self._chunks):
            # Сохраняем промежуточные ответы в БД? Нет — храним в view.
            # Показываем кнопку "Продолжить".
            view = NextChunkView(
                self.ticket_type, self.config, self.user,
                self._all_answers, next_idx, len(self._chunks),
            )
            try:
                await interaction.response.send_message(
                    embed=build_success(
                        title=f"✅ Часть {self._chunk_index + 1}/{len(self._chunks)} сохранена",
                        description=(
                            f"Ответы приняты. Осталось заполнить ещё "
                            f"**{len(self._chunks) - next_idx}** часть анкеты.\n\n"
                            f"👇 Нажмите кнопку ниже, чтобы продолжить."
                        ),
                    ),
                    view=view,
                    ephemeral=True,
                )
                # Сохраняем ссылку на сообщение для on_timeout
                try:
                    view.message = await interaction.original_response()
                except (discord.HTTPException, AttributeError):
                    pass
            except discord.HTTPException as e:
                log.warning("Не удалось отправить сообщение-переход: %s", e)
                # Запасной вариант: пробуем старый способ
                next_modal = ApplicationModal(
                    self.ticket_type, self.config, self.user,
                    self._all_answers, next_idx,
                )
                try:
                    await interaction.response.send_modal(next_modal)
                except discord.HTTPException as e2:
                    log.error("Все способы открыть следующую модалку провалились: %s", e2)
                    try:
                        await interaction.followup.send(
                            embed=build_error(
                                description=(
                                    "Не удалось открыть следующую часть анкеты. "
                                    f"Попробуйте ещё раз или обратитесь к администратору. "
                                    f"Ошибка: `{e2}`"
                                ),
                            ),
                            ephemeral=True,
                        )
                    except discord.HTTPException:
                        pass
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

        # 9. Анкета — отдельный embed (закреплённый).
        # ⚠️ Discord лимит: embed total ≤ 6000 символов. При 15 вопросах с
        # длинными ответами не помещается → разбиваем на несколько embed'ов.
        form_embeds = []
        cur_embed = discord.Embed(
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
        cur_embed.set_thumbnail(url=user.display_avatar.url)
        cur_embed.set_footer(text="EGODiscord System • Анкета кандидата")
        form_embeds.append(cur_embed)

        # Эвристика: если в одном embed'е сумма длин полей + описание > 4500
        # символов — начинаем новый embed.
        cur_total = len(cur_embed.description or "")
        MAX_PER_EMBED = 4500  # оставляем запас под title/footer/thumbnail url

        for i, (q, a) in enumerate(answers, 1):
            field_name = f"❓ {i}. {q['title'][:250]}"
            field_val = (a[:1024] if a else "—")
            field_len = len(field_name) + len(field_val) + 4

            if cur_total + field_len > MAX_PER_EMBED and len(cur_embed.fields) > 0:
                # Начинаем новый embed
                cur_embed = discord.Embed(
                    title=f"📋 Анкета кандидата (продолжение {len(form_embeds) + 1})",
                    description="",
                    color=COLOR_MAIN,
                    timestamp=embeds.now_msk(),
                )
                cur_embed.set_footer(text="EGODiscord System • Анкета кандидата (продолжение)")
                form_embeds.append(cur_embed)
                cur_total = 0

            cur_embed.add_field(name=field_name, value=field_val, inline=False)
            cur_total += field_len

        try:
            # Отправляем все embed'ы, первый закрепляем
            form_msgs = []
            for idx, fe in enumerate(form_embeds):
                m = await channel.send(embed=fe)
                form_msgs.append(m)
                if idx == 0:
                    try:
                        await m.pin()
                    except discord.HTTPException:
                        pass
            form_msg = form_msgs[0] if form_msgs else None
        except discord.HTTPException as e:
            log.warning("Не удалось отправить/закрепить анкету: %s", e)
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
                    "▸ Поиск пиратки (Spacewar) .. ожидание\n"
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
        vac_banned = bool(result.get("vac_banned"))
        community_banned = bool(result.get("community_banned"))
        profile_state = result.get("profile_state", "public")
        hours_rust = result.get("hours_rust")
        is_pirate = bool(result.get("is_pirate"))
        pirate_evidence = result.get("pirate_evidence") or []

        if vac_banned or community_banned:
            color = COLOR_ERROR
            if vac_banned and community_banned:
                status_text = "🔴 VAC + Community бан"
            elif vac_banned:
                status_text = "🔴 VAC-бан"
            else:
                status_text = "🔴 Community-бан"
            risk_emoji = "⚠️"
        elif is_pirate:
            # Пиратка (Spacewar вместо купленной Rust) — оранжевый цвет.
            # Бан важнее, но пирата мы тоже берём — просто с жёстким ограничением.
            color = COLOR_PIRATE
            status_text = "🏴‍☠️ Пират (Spacewar)"
            risk_emoji = "🏴‍☠️"
        elif profile_state == "private":
            color = COLOR_WARNING
            status_text = "🟡 Профиль приватный"
            risk_emoji = "⚠️"
        else:
            color = COLOR_SUCCESS
            status_text = "🟢 Аккаунт чистый"
            risk_emoji = "✅"

        # ── Часы в Rust ──────────────────────────────────────────────────────
        source = result.get("source", "unknown")
        if hours_rust is None:
            if profile_state == "private":
                hours_text = "🔒 Скрыто (приватный профиль)"
                hours_emoji = "🔒"
            elif source in ("html", "mixed") and not api_key:
                hours_text = "ℹ️ Требуется Steam API ключ"
                hours_emoji = "ℹ️"
            else:
                hours_text = "❌ Rust не найдена в библиотеке"
                hours_emoji = "❌"
        else:
            hours = hours_rust
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

        if vac_banned or community_banned:
            title = "🚨 ОБНАРУЖЕН БАН!"
        elif is_pirate:
            title = "🏴‍☠️ ОБНАРУЖЕН ПИРАТ!"
        elif profile_state == "private":
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

        if is_pirate:
            description += (
                "\n\n🏴‍☠️ **Обнаружена пиратская версия Rust!**\n"
                "Кандидат играет через **Spacewar** (тестовое приложение Steam, "
                "которое пираты используют для запуска Rust без лицензии).\n"
                "⚠️ **Принимать с жёстким ограничением** — на усмотрение "
                "лидерства, после дополнительной проверки."
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

        # ── Признаки пиратки ──────────────────────────────────────────────────
        if is_pirate and pirate_evidence:
            # Объединяем все признаки в одно поле (лимит 1024 символа)
            evidence_text = "\n".join(f"• {e}" for e in pirate_evidence)
            if len(evidence_text) > 1000:
                evidence_text = evidence_text[:1000] + "\n…(обрезано)"
            embed.add_field(
                name="🏴‍☠️ Признаки пиратки",
                value=evidence_text,
                inline=False,
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

        placeholder, options = _get_panel_select_options(config)
        select = ui.Select(
            custom_id="ego_ticket_panel_select",
            placeholder=placeholder[:100] if placeholder else None,
            min_values=1, max_values=1,
            options=options,
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
        # Сначала defer — channel.send может занять >3 сек на медленном API,
        # и interaction протухнет. С defer — можем потом followup.send.
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException:
            pass

        embed = _build_panel_embed(self.config)
        view = TicketPanelView(self.config)
        try:
            await interaction.channel.send(embed=embed, view=view)
            await interaction.followup.send(
                embed=build_success(
                    title="✅ Панель создана",
                    description=f"Панель тикетов отправлена в {interaction.channel.mention}.",
                ),
                ephemeral=True,
            )
        except discord.HTTPException as e:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        embed=build_error(description=f"Не удалось создать панель: `{e}`"),
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        embed=build_error(description=f"Не удалось создать панель: `{e}`"),
                        ephemeral=True,
                    )
            except discord.HTTPException:
                pass

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
        sent_msg = await interaction.channel.send(embed=embed, view=help_view)
        help_view.message = sent_msg
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
    # bot._config уже установлен в bot.py после создания бота.
    # Если по какой-то причине нет — загружаем здесь как fallback.
    if not getattr(bot, "_config", None):
        import json
        with open("config.json", "r", encoding="utf-8") as f:
            bot._config = json.load(f)
    await bot.add_cog(Tickets(bot))
