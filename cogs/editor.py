"""
cogs/editor.py — Удобный редактор бота для администрации EGO.

Изменения:
    • Дашборд теперь через DROPDOWN-меню вместо кучи кнопок
    • Редактор вопросов полностью переписан:
        - Поддержка до 15 вопросов (вместо 5)
        - Каждый вопрос имеет: title, subtitle (placeholder), max_length,
          min_length, multiline, required, is_real_name, is_steam
        - Редактирование по индексу: выбрал в dropdown нужный вопрос → модалка
    • Все изменения сразу сохраняются в config.json и bot._config

Команды:
    .editor              — открыть дашборд (dropdown)
    .editor questions    — открыть раздел вопросов напрямую
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Any

import discord
from discord import ui
from discord.ext import commands

from utils import embeds
from utils.embeds import (
    build_main, build_success, build_error, build_warning, build_info,
    msk_timestamp, COLOR_MAIN, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
)

log = logging.getLogger(__name__)


# ============================================================================
# Утилиты
# ============================================================================

CONFIG_PATH = Path("config.json")


def _save_config(config: dict) -> bool:
    """Атомарно сохраняет config.json. Возвращает True при успехе."""
    try:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        tmp.replace(CONFIG_PATH)
        return True
    except Exception as e:
        log.exception("Не удалось сохранить config.json: %s", e)
        return False


def _is_admin(member: discord.Member, config: dict) -> bool:
    role_ids = set(r.id for r in member.roles)
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return member.guild_permissions.administrator


def _is_dev(user: discord.abc.User, config: dict) -> bool:
    return user.id == config.get("developer_id", 0)


def _truncate(s: str, n: int = 100) -> str:
    if not s:
        return "—"
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _list_preview(items: list, max_n: int = 5, empty: str = "—") -> str:
    if not items:
        return empty
    if len(items) <= max_n:
        return "\n".join(f"• {_truncate(str(i), 80)}" for i in items)
    head = "\n".join(f"• {_truncate(str(i), 80)}" for i in items[:max_n])
    return f"{head}\n• …и ещё {len(items) - max_n}"


def _roles_to_str(role_ids: list, guild: discord.Guild) -> str:
    parts = []
    for rid in role_ids or []:
        role = guild.get_role(rid)
        if role:
            parts.append(f"{role.mention} (`{rid}`)")
        else:
            parts.append(f"❓ `{rid}` (не найдена)")
    return "\n".join(parts) if parts else "—"


def _normalize_question(q: Any) -> dict:
    """Приводит вопрос к единому формату dict."""
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
    if not isinstance(questions, list):
        return []
    return [_normalize_question(q) for q in questions if q]


# ============================================================================
# Дашборд (главное меню) — DROPDOWN вместо кучи кнопок
# ============================================================================

def _dashboard_embed(config: dict) -> discord.Embed:
    """Собирает embed-превью текущих настроек бота."""
    questions_clan = _normalize_questions(config.get("questions_clan", []))
    questions_mod = _normalize_questions(config.get("questions_mod", []))
    steam_key = config.get("steam_api_key", "")
    masked_key = (steam_key[:6] + "…" + steam_key[-4:]) if len(steam_key) > 10 else "—"
    roles_cfg = config.get("roles", {})

    embed_color_str = config.get("embed_color", "5865F2")
    try:
        embed_color_int = int(embed_color_str, 16)
    except (ValueError, TypeError):
        embed_color_int = 0x5865F2
        embed_color_str = "5865F2"

    welcome_text = config.get("ticket_welcome_text", "")
    branding_url = config.get("brand_thumbnail_url", "")

    embed = discord.Embed(
        title="🛠️ Редактор бота EGO",
        description=(
            f"## ⚙️ Текущие настройки\n\n"
            f"Все изменения применяются **мгновенно** — без перезапуска бота.\n"
            f"Выберите раздел в **выпадающем списке** ниже.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"### 📋 Анкеты\n"
            f"🛡️ **Вопросы (Клан):** {len(questions_clan)} шт.\n"
            f"👑 **Вопросы (Модерация):** {len(questions_mod)} шт.\n\n"
            f"### 🎨 Дизайн\n"
            f"📝 **Текст панели:** {_truncate(config.get('ticket_panel_text', ''), 50)}\n"
            f"👋 **Приветствие:** {_truncate(welcome_text or '_(по умолчанию)_', 50)}\n"
            f"🎨 **Цвет embed:** `#{embed_color_str}`\n"
            f"🖼️ **Брендинг:** {'✅ задан' if branding_url else '_(по умолчанию)_'}\n\n"
            f"### 🔑 Ключи и роли\n"
            f"🔑 **Steam API:** `{masked_key}`\n"
            f"🔔 **Пинг ролей (Клан/Модер):** "
            f"{len(config.get('ping_roles_clan', []))} / "
            f"{len(config.get('ping_roles_mod', []))}\n"
            f"👑 **Ролей персонала:** {len([k for k, v in roles_cfg.items() if v])} / 7\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 Выберите раздел в меню ниже"
        ),
        color=embed_color_int,
        timestamp=embeds.now_msk(),
    )
    embed.set_footer(text="EGODiscord System • Editor Dashboard")
    if branding_url:
        embed.set_thumbnail(url=branding_url)
    return embed


class EditorDashboardView(ui.View):
    """Дашборд редактора с DROPDOWN-меню вместо кучи кнопок."""

    def __init__(self, config: dict, owner_id: int):
        super().__init__(timeout=300)
        self.config = config
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

        # Создаём dropdown с разделами
        select = ui.Select(
            placeholder="🛠️ Выберите раздел для настройки...",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(
                    label="Вопросы — Клан",
                    description=f"Редактировать анкету клана ({len(_normalize_questions(config.get('questions_clan', [])))} шт.)",
                    emoji="🛡️",
                    value="q_clan",
                ),
                discord.SelectOption(
                    label="Вопросы — Модерация",
                    description=f"Редактировать анкету модерации ({len(_normalize_questions(config.get('questions_mod', [])))} шт.)",
                    emoji="👑",
                    value="q_mod",
                ),
                discord.SelectOption(
                    label="Текст панели",
                    description="Текст панели тикетов (Markdown)",
                    emoji="📝",
                    value="panel_text",
                ),
                discord.SelectOption(
                    label="Внешний вид панели",
                    description="Заголовок, поля, эмодзи, лейблы опций, футер, иконка",
                    emoji="🎨",
                    value="panel_appearance",
                ),
                discord.SelectOption(
                    label="Steam API ключ",
                    description="Ключ для проверки VAC/часов в Rust",
                    emoji="🔑",
                    value="steam_key",
                ),
                discord.SelectOption(
                    label="Пинг-роли",
                    description="Какие роли пинговать при создании тикета",
                    emoji="🔔",
                    value="ping_roles",
                ),
                discord.SelectOption(
                    label="Роли персонала",
                    description="ID ролей лидер/админ/модератор/хелпер",
                    emoji="👑",
                    value="staff_roles",
                ),
                discord.SelectOption(
                    label="Каналы и категории",
                    description="ID каналов логов, категорий тикетов, роль EGO",
                    emoji="📁",
                    value="channels",
                ),
                discord.SelectOption(
                    label="Цвет embed",
                    description="Цвет всех embed'ов бота (8 пресетов + HEX)",
                    emoji="🎨",
                    value="color",
                ),
                discord.SelectOption(
                    label="Приветствие в тикете",
                    description="Кастомный текст приветствия",
                    emoji="👋",
                    value="welcome",
                ),
                discord.SelectOption(
                    label="Брендинг (иконка)",
                    description="URL иконки для embed'ов",
                    emoji="🖼️",
                    value="branding",
                ),
                discord.SelectOption(
                    label="👁 Превью панели",
                    description="Посмотреть как выглядит панель",
                    emoji="👁",
                    value="preview",
                ),
                discord.SelectOption(
                    label="♻️ Пересоздать панель",
                    description="Удалить старую панель и создать новую",
                    emoji="♻️",
                    value="recreate",
                ),
                discord.SelectOption(
                    label="⚠️ Сбросить настройки",
                    description="Сброс вопросов, текста, цвета (ID каналов сохранятся)",
                    emoji="⚠️",
                    value="reset",
                ),
                discord.SelectOption(
                    label="✖️ Закрыть редактор",
                    description="Убрать это сообщение",
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
                    description="Этот дашборд открыл другой администратор. "
                                "Используйте `.editor`, чтобы открыть свой."
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
        try:
            value = interaction.data["values"][0]
        except (KeyError, IndexError, TypeError):
            return

        if value == "q_clan":
            await self._open_questions_editor(interaction, "clan")
        elif value == "q_mod":
            await self._open_questions_editor(interaction, "mod")
        elif value == "panel_text":
            await interaction.response.send_modal(EditPanelTextModal(self.config, self))
        elif value == "panel_appearance":
            # Показываем подменю с 3 кнопками: тексты / опции / доп
            view = PanelAppearanceMenuView(self.config, self)
            await interaction.response.send_message(
                embed=_panel_appearance_menu_embed(self.config),
                view=view,
                ephemeral=True,
            )
        elif value == "steam_key":
            await interaction.response.send_modal(EditSteamKeyModal(self.config, self))
        elif value == "ping_roles":
            await interaction.response.send_modal(EditPingRolesModal(self.config, self))
        elif value == "staff_roles":
            await interaction.response.send_modal(EditRolesModal(self.config, self))
        elif value == "channels":
            await interaction.response.send_modal(EditChannelIdsModal(self.config, self))
        elif value == "color":
            await interaction.response.send_modal(EditEmbedColorModal(self.config, self))
        elif value == "welcome":
            await interaction.response.send_modal(EditWelcomeMessageModal(self.config, self))
        elif value == "branding":
            await interaction.response.send_modal(EditBrandingModal(self.config, self))
        elif value == "preview":
            await self._preview_panel(interaction)
        elif value == "recreate":
            await self._recreate_panel(interaction)
        elif value == "reset":
            await self._reset_settings(interaction)
        elif value == "close":
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass

    async def _open_questions_editor(self, interaction: discord.Interaction,
                                      ticket_type: str):
        """Открывает dropdown-редактор вопросов для выбранного типа."""
        view = QuestionsEditorView(self.config, ticket_type, self)
        msg = await interaction.response.send_message(
            embed=_build_questions_editor_embed(self.config, ticket_type),
            view=view,
            ephemeral=True,
        )
        # discord.py 2.x: response.send_message для modal-followup
        # сохраняем message через followup
        try:
            view.message = await interaction.original_response()
        except (discord.HTTPException, AttributeError):
            pass

    async def _preview_panel(self, interaction: discord.Interaction):
        from cogs.tickets import _build_panel_embed
        embed = _build_panel_embed(self.config)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _recreate_panel(self, interaction: discord.Interaction):
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

        from cogs.tickets import TicketPanelView, _build_panel_embed
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

    async def _reset_settings(self, interaction: discord.Interaction):
        confirm_view = ConfirmResetView(self)
        await interaction.response.send_message(
            embed=build_warning(
                title="⚠️ Подтверждение сброса",
                description=(
                    "Будут сброшены:\n"
                    "• Вопросы анкеты (клан и модерация)\n"
                    "• Текст панели тикетов\n"
                    "• Приветствие в тикете\n"
                    "• Цвет embed'ов\n"
                    "• Брендинг (иконка)\n"
                    "• Пинг-роли\n\n"
                    "**Сохранятся:** ID каналов и категорий, роли персонала, "
                    "Steam API ключ, developer_id."
                ),
            ),
            view=confirm_view,
            ephemeral=True,
        )


# ============================================================================
# Редактор вопросов — DROPDOWN для выбора вопроса + модалка
# ============================================================================

def _build_questions_editor_embed(config: dict, ticket_type: str) -> discord.Embed:
    """Embed со списком вопросов и подсказками."""
    is_clan = ticket_type == "clan"
    key = "questions_clan" if is_clan else "questions_mod"
    questions = _normalize_questions(config.get(key, []))

    type_emoji = "🛡️" if is_clan else "👑"
    type_label = "Клан" if is_clan else "Модерация"

    description = (
        f"## {type_emoji} Редактор вопросов — {type_label}\n\n"
        f"**Текущее количество вопросов:** {len(questions)} из 15\n\n"
        f"### 📋 Текущие вопросы\n"
    )

    if not questions:
        description += "_Нет вопросов. Добавьте первый через меню ниже._\n"
    else:
        for i, q in enumerate(questions, 1):
            flags = []
            if q.get("is_real_name"):
                flags.append("ИМЯ")
            if q.get("is_steam"):
                flags.append("STEAM")
            if q.get("multiline"):
                flags.append("многостр.")
            if not q.get("required"):
                flags.append("необяз.")
            flags_str = f" [{' • '.join(flags)}]" if flags else ""
            description += (
                f"**{i}.** {q['title']} `{q.get('max_length', 500)}`{flags_str}\n"
                f"   _{q.get('subtitle', '') or '—'}_\n"
            )

    description += (
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"### 👇 Выберите действие в меню\n"
        f"• **Изменить вопрос №X** — открыть модалку редактирования\n"
        f"• **➕ Добавить вопрос** — создать новый (макс. 15)\n"
        f"• **❌ Удалить вопрос №X** — удалить по индексу\n"
        f"• **⬆ Вверх / ⬇ Вниз** — переместить вопрос\n"
        f"• **↩ Назад** — вернуться в главное меню\n"
    )

    embed = discord.Embed(
        title=f"{type_emoji} Вопросы — {type_label}",
        description=description,
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )
    embed.set_footer(text="EGODiscord System • Questions Editor")
    return embed


class QuestionsEditorView(ui.View):
    """Dropdown-меню для редактирования вопросов."""

    def __init__(self, config: dict, ticket_type: str,
                 parent_view: EditorDashboardView):
        super().__init__(timeout=300)
        self.config = config
        self.ticket_type = ticket_type
        self.parent_view = parent_view
        self.message: Optional[discord.Message] = None

        key = "questions_clan" if ticket_type == "clan" else "questions_mod"
        questions = _normalize_questions(config.get(key, []))

        # Строим опции: edit N, add, delete N, move up/down N, back
        options = []

        # Действия для каждого вопроса
        for i, q in enumerate(questions, 1):
            title_short = q["title"][:40] if q["title"] else f"Вопрос {i}"
            options.append(discord.SelectOption(
                label=f"✏️ Изменить #{i}: {title_short}",
                description=f"Редактировать вопрос (сейчас: max {q.get('max_length', 500)} симв.)",
                value=f"edit_{i}",
            ))

        # Добавить
        if len(questions) < 15:
            options.append(discord.SelectOption(
                label="➕ Добавить новый вопрос",
                description=f"Создать вопрос №{len(questions) + 1} (макс. 15)",
                value="add",
            ))

        # Удалить
        for i, q in enumerate(questions, 1):
            title_short = q["title"][:40] if q["title"] else f"Вопрос {i}"
            options.append(discord.SelectOption(
                label=f"❌ Удалить #{i}: {title_short}",
                description="Вопрос будет удалён безвозвратно",
                value=f"del_{i}",
            ))

        # Переместить вверх/вниз
        for i, q in enumerate(questions, 1):
            if i > 1:
                options.append(discord.SelectOption(
                    label=f"⬆ Вверх #{i}",
                    description=f"Поднять вопрос №{i} на позицию {i-1}",
                    value=f"up_{i}",
                ))
            if i < len(questions):
                options.append(discord.SelectOption(
                    label=f"⬇ Вниз #{i}",
                    description=f"Опустить вопрос №{i} на позицию {i+1}",
                    value=f"down_{i}",
                ))

        # Назад
        back_option = discord.SelectOption(
            label="↩ Назад в главное меню",
            description="Вернуться к дашборду редактора",
            value="back",
        )

        # Discord лимит — 25 опций в select.
        # Резервируем последний слот под "Назад" — иначе при 25+ опциях
        # кнопка "Назад" отсекается, и пользователь не может вернуться.
        # Берём первые 24 action-опции + 1 back = 25 всего.
        MAX_ACTION_OPTIONS = 24
        if len(options) > MAX_ACTION_OPTIONS:
            # Сохраняем приоритет: edit > add > del > up/down
            # У edit (15) и del (15) — по 15 опций, у up/down — по 14.
            # Если их слишком много, обрезаем edit/del/up/down пропорционально.
            # Простейший вариант: edit+add без обрезки, остальное обрезаем.
            edit_opts = [o for o in options if o.value.startswith("edit_")]
            add_opts  = [o for o in options if o.value == "add"]
            del_opts  = [o for o in options if o.value.startswith("del_")]
            move_opts = [o for o in options if o.value.startswith(("up_", "down_"))]

            # Оставляем как можно больше edit (самое важное), потом add, потом del, потом move
            remaining = MAX_ACTION_OPTIONS - len(add_opts)  # add всегда включаем
            if remaining < 0:
                # Экстремальный случай: даже add не помещается
                add_opts = add_opts[:MAX_ACTION_OPTIONS]
                edit_opts, del_opts, move_opts = [], [], []
            else:
                if len(edit_opts) > remaining:
                    edit_opts = edit_opts[:remaining]
                remaining -= len(edit_opts)
                if remaining > 0 and len(del_opts) > remaining:
                    del_opts = del_opts[:remaining]
                remaining -= len(del_opts)
                if remaining > 0 and len(move_opts) > remaining:
                    move_opts = move_opts[:remaining]

            options = edit_opts + add_opts + del_opts + move_opts

        options.append(back_option)

        select = ui.Select(
            placeholder="⚡ Выберите вопрос или действие...",
            min_values=1, max_values=1,
            options=options,
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_select(self, interaction: discord.Interaction):
        try:
            value = interaction.data["values"][0]
        except (KeyError, IndexError, TypeError):
            return

        if value == "back":
            try:
                await interaction.response.edit_message(
                    embed=_dashboard_embed(self.config),
                    view=None,
                )
            except discord.HTTPException:
                pass
            return

        if value == "add":
            # Открываем модалку для нового вопроса
            key = "questions_clan" if self.ticket_type == "clan" else "questions_mod"
            questions = _normalize_questions(self.config.get(key, []))
            if len(questions) >= 15:
                await interaction.response.send_message(
                    embed=build_error(description="Достигнут лимит — 15 вопросов максимум."),
                    ephemeral=True,
                )
                return
            modal = EditSingleQuestionModal(
                self.config, self.ticket_type, len(questions) + 1,
                None, self,
            )
            await interaction.response.send_modal(modal)
            return

        if value.startswith("edit_"):
            idx = int(value.split("_")[1]) - 1
            key = "questions_clan" if self.ticket_type == "clan" else "questions_mod"
            questions = _normalize_questions(self.config.get(key, []))
            if 0 <= idx < len(questions):
                modal = EditSingleQuestionModal(
                    self.config, self.ticket_type, idx + 1,
                    questions[idx], self,
                )
                await interaction.response.send_modal(modal)
            return

        if value.startswith("del_"):
            idx = int(value.split("_")[1]) - 1
            key = "questions_clan" if self.ticket_type == "clan" else "questions_mod"
            questions = _normalize_questions(self.config.get(key, []))
            if 0 <= idx < len(questions):
                removed = questions.pop(idx)
                self.config[key] = questions
                if _save_config(self.config):
                    await interaction.response.send_message(
                        embed=build_success(
                            title="✅ Вопрос удалён",
                            description=f"Удалён: **{removed['title']}**",
                        ),
                        ephemeral=True,
                    )
                    # Обновляем список
                    try:
                        new_view = QuestionsEditorView(
                            self.config, self.ticket_type, self.parent_view,
                        )
                        new_view.message = self.message
                        await self.message.edit(
                            embed=_build_questions_editor_embed(self.config, self.ticket_type),
                            view=new_view,
                        )
                    except (discord.HTTPException, AttributeError):
                        pass
                else:
                    await interaction.response.send_message(
                        embed=build_error(description="Не удалось сохранить."),
                        ephemeral=True,
                    )
            return

        if value.startswith("up_") or value.startswith("down_"):
            idx = int(value.split("_")[1]) - 1
            direction = 1 if value.startswith("down_") else -1
            new_idx = idx + direction
            key = "questions_clan" if self.ticket_type == "clan" else "questions_mod"
            questions = _normalize_questions(self.config.get(key, []))
            if 0 <= idx < len(questions) and 0 <= new_idx < len(questions):
                questions[idx], questions[new_idx] = questions[new_idx], questions[idx]
                self.config[key] = questions
                if _save_config(self.config):
                    await interaction.response.send_message(
                        embed=build_success(
                            title="✅ Вопрос перемещён",
                            description=f"Перемещён с позиции {idx+1} на {new_idx+1}",
                        ),
                        ephemeral=True,
                    )
                    try:
                        new_view = QuestionsEditorView(
                            self.config, self.ticket_type, self.parent_view,
                        )
                        new_view.message = self.message
                        await self.message.edit(
                            embed=_build_questions_editor_embed(self.config, self.ticket_type),
                            view=new_view,
                        )
                    except (discord.HTTPException, AttributeError):
                        pass
                else:
                    await interaction.response.send_message(
                        embed=build_error(description="Не удалось сохранить."),
                        ephemeral=True,
                    )
            return


class EditSingleQuestionModal(ui.Modal):
    """Редактирование одного вопроса со всеми полями."""

    def __init__(self, config: dict, ticket_type: str, question_num: int,
                 existing: Optional[dict], parent_view: QuestionsEditorView):
        is_clan = ticket_type == "clan"
        type_emoji = "🛡️" if is_clan else "👑"
        if existing:
            title = f"{type_emoji} Вопрос #{question_num} (изм.)"
        else:
            title = f"{type_emoji} Новый вопрос #{question_num}"
        super().__init__(title=title[:45])

        self.config = config
        self.ticket_type = ticket_type
        self.question_num = question_num
        self.existing = existing
        self.parent_view = parent_view

        # Поля формы
        self.title_input = ui.TextInput(
            label="Название вопроса",
            placeholder="Например: Сколько вам лет",
            default=existing["title"] if existing else "",
            required=True,
            max_length=45,
            style=discord.TextStyle.short,
        )
        self.add_item(self.title_input)

        self.subtitle_input = ui.TextInput(
            label="Подвопросник (placeholder)",
            placeholder="Например: Напишите свой возраст",
            default=existing.get("subtitle", "") if existing else "",
            required=False,
            max_length=100,
            style=discord.TextStyle.short,
        )
        self.add_item(self.subtitle_input)

        self.max_length_input = ui.TextInput(
            label="Макс. символов в ответе (1-4000)",
            placeholder="2",
            default=str(existing.get("max_length", 500)) if existing else "500",
            required=True,
            max_length=4,
            style=discord.TextStyle.short,
        )
        self.add_item(self.max_length_input)

        self.min_length_input = ui.TextInput(
            label="Мин. символов (0 = без минимума)",
            placeholder="1",
            default=str(existing.get("min_length", 1)) if existing else "1",
            required=False,
            max_length=4,
            style=discord.TextStyle.short,
        )
        self.add_item(self.min_length_input)

        self.flags_input = ui.TextInput(
            label="Флаги: multiline, required, real_name, steam",
            placeholder="Например: required, multiline",
            default=self._flags_to_str(existing) if existing else "required",
            required=True,
            max_length=80,
            style=discord.TextStyle.short,
        )
        self.add_item(self.flags_input)

    @staticmethod
    def _flags_to_str(q: dict) -> str:
        flags = []
        if q.get("multiline"):
            flags.append("multiline")
        if q.get("required"):
            flags.append("required")
        if q.get("is_real_name"):
            flags.append("real_name")
        if q.get("is_steam"):
            flags.append("steam")
        return ", ".join(flags) if flags else ""

    async def on_submit(self, interaction: discord.Interaction):
        # Парсим значения
        title = self.title_input.value.strip()
        if not title:
            await interaction.response.send_message(
                embed=build_error(description="Название вопроса не может быть пустым."),
                ephemeral=True,
            )
            return

        subtitle = self.subtitle_input.value.strip()

        try:
            max_length = max(1, min(int(self.max_length_input.value.strip()), 4000))
        except ValueError:
            await interaction.response.send_message(
                embed=build_error(description="Макс. символов должно быть числом от 1 до 4000."),
                ephemeral=True,
            )
            return

        try:
            min_length = max(0, int(self.min_length_input.value.strip() or "0"))
        except ValueError:
            min_length = 0

        if min_length > max_length:
            await interaction.response.send_message(
                embed=build_error(description="Мин. символов не может быть больше макс."),
                ephemeral=True,
            )
            return

        # Парсим флаги
        flags_str = self.flags_input.value.lower()
        flags = {f.strip() for f in flags_str.split(",") if f.strip()}
        multiline = "multiline" in flags
        required = "required" in flags or not flags
        is_real_name = "real_name" in flags or "имя" in flags or "name" in flags
        is_steam = "steam" in flags or "стим" in flags

        new_question = {
            "title": title,
            "subtitle": subtitle,
            "max_length": max_length,
            "min_length": min_length,
            "multiline": multiline,
            "required": required,
            "is_real_name": is_real_name,
            "is_steam": is_steam,
        }

        # Сохраняем в config
        key = "questions_clan" if self.ticket_type == "clan" else "questions_mod"
        questions = _normalize_questions(self.config.get(key, []))

        if self.existing:
            # Редактируем существующий
            idx = self.question_num - 1
            if 0 <= idx < len(questions):
                questions[idx] = new_question
            else:
                questions.append(new_question)
        else:
            # Добавляем новый
            if len(questions) >= 15:
                await interaction.response.send_message(
                    embed=build_error(description="Достигнут лимит — 15 вопросов максимум."),
                    ephemeral=True,
                )
                return
            questions.append(new_question)

        self.config[key] = questions
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return

        # Подтверждение
        flags_display = []
        if multiline:
            flags_display.append("📝 многостр.")
        if required:
            flags_display.append("✅ обяз.")
        else:
            flags_display.append("⭕ необяз.")
        if is_real_name:
            flags_display.append("👤 имя")
        if is_steam:
            flags_display.append("🎮 steam")

        await interaction.response.send_message(
            embed=build_success(
                title=f"✅ Вопрос #{self.question_num} сохранён",
                description=(
                    f"**Название:** {title}\n"
                    f"**Подсказка:** {subtitle or '—'}\n"
                    f"**Длина:** {min_length}-{max_length} симв.\n"
                    f"**Флаги:** {' • '.join(flags_display)}"
                ),
            ),
            ephemeral=True,
        )

        # Обновляем список
        try:
            new_view = QuestionsEditorView(
                self.config, self.ticket_type,
                self.parent_view.parent_view,
            )
            # Переносим message из старого view в новый (чтобы timeout работал)
            new_view.message = self.parent_view.message
            await self.parent_view.message.edit(
                embed=_build_questions_editor_embed(self.config, self.ticket_type),
                view=new_view,
            )
            # Обновляем ссылку в parent_view, чтобы старый view тоже видел новый
            # (для согласованности, хотя после edit старый view уже не активен)
        except (discord.HTTPException, AttributeError):
            pass


# ============================================================================
# Остальные модалки (текст панели, Steam, роли, каналы, цвет, приветствие,
# брендинг, пинг-роли) — без изменений
# ============================================================================

class EditPanelTextModal(ui.Modal):
    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="📝 Текст панели тикетов")
        self.config = config
        self.parent_view = parent_view
        self.text_input = ui.TextInput(
            label="Текст панели",
            placeholder="Поддерживает Markdown (# заголовки, **жирный**, эмодзи)",
            default=config.get("ticket_panel_text", ""),
            required=True,
            max_length=1900,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_text = self.text_input.value.strip()
        if not new_text:
            await interaction.response.send_message(
                embed=build_error(description="Текст не может быть пустым."),
                ephemeral=True,
            )
            return
        self.config["ticket_panel_text"] = new_text
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_success(
                title="✅ Текст панели обновлён",
                description=f"Новый текст:\n\n{_truncate(new_text, 1500)}",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


# ============================================================================
# Внешний вид панели (заголовок, поля, опции dropdown, футер, иконка)
# ============================================================================

def _panel_appearance_menu_embed(config: dict) -> discord.Embed:
    """Embed-меню выбора группы настроек внешнего вида панели."""
    # Собираем краткий превью текущих значений
    title = config.get("panel_title") or "_(по умолчанию)_"
    clan_label = config.get("panel_clan_option_label") or "Набор в клан"
    clan_emoji = config.get("panel_clan_option_emoji") or "🛡️"
    mod_label = config.get("panel_mod_option_label") or "Набор в модерацию"
    mod_emoji = config.get("panel_mod_option_emoji") or "👑"
    placeholder = config.get("panel_select_placeholder") or "_(по умолчанию)_"
    footer = config.get("panel_footer") or "_(по умолчанию)_"
    thumb = config.get("panel_thumbnail_url") or "_(по умолчанию)_"

    embed = discord.Embed(
        title="🎨 Внешний вид панели",
        description=(
            "## Выберите группу настроек\n\n"
            "Все поля необязательные — пустое значение = использовать дефолт.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "### 📋 Текущие значения\n"
            f"📝 **Заголовок:** {_truncate(title, 60)}\n"
            f"🎫 **Placeholder:** {_truncate(placeholder, 60)}\n"
            f"{clan_emoji} **Опция «Клан»:** {_truncate(clan_label, 40)}\n"
            f"{mod_emoji} **Опция «Модерация»:** {_truncate(mod_label, 40)}\n"
            f"👣 **Футер:** {_truncate(footer, 60)}\n"
            f"🖼️ **Иконка:** {_truncate(thumb, 60)}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "### 👇 Нажмите кнопку ниже"
        ),
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )
    embed.set_footer(text="EGODiscord System • Panel Appearance")
    return embed


class PanelAppearanceMenuView(ui.View):
    """Подменю с 3 кнопками для выбора группы настроек внешнего вида панели."""

    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(timeout=120)
        self.config = config
        self.parent_view = parent_view

    @ui.button(label="Тексты (заголовок, поля)", emoji="📝",
               style=discord.ButtonStyle.primary, custom_id="ego_panel_app_texts")
    async def btn_texts(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(
            EditPanelAppearanceModal(self.config, self.parent_view)
        )

    @ui.button(label="Опции dropdown", emoji="🎫",
               style=discord.ButtonStyle.primary, custom_id="ego_panel_app_options")
    async def btn_options(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(
            EditPanelDropdownModal(self.config, self.parent_view)
        )

    @ui.button(label="Как это работает + футер + иконка", emoji="🧩",
               style=discord.ButtonStyle.primary, custom_id="ego_panel_app_extras")
    async def btn_extras(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(
            EditPanelExtrasModal(self.config, self.parent_view)
        )

    @ui.button(label="↩ Назад", emoji="↩",
               style=discord.ButtonStyle.secondary, custom_id="ego_panel_app_back")
    async def btn_back(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.edit_message(
                embed=_dashboard_embed(self.config),
                view=None,
            )
        except discord.HTTPException:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Только владелец родительского дашборда может жать кнопки
        if interaction.user.id != self.parent_view.owner_id:
            await interaction.response.send_message(
                embed=build_error(
                    description="Это меню открыл другой администратор."
                ),
                ephemeral=True,
            )
            return False
        return True


class EditPanelAppearanceModal(ui.Modal):
    """Редактирование заголовка, описания, полей "Набор в клан/модерацию",
    поля "Как это работает" и футера.

    Discord лимит — 5 TextInput на модалку. Поэтому разбито на 2 модалки:
    эта (основные тексты) и EditPanelDropdownModal (опции dropdown + иконка).
    """

    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="🎨 Внешний вид панели — тексты")
        self.config = config
        self.parent_view = parent_view

        self.title_input = ui.TextInput(
            label="Заголовок embed'а панели",
            placeholder="🛡️ СИСТЕМА НАБОРА EGO",
            default=config.get("panel_title", ""),
            required=False, max_length=200, style=discord.TextStyle.short,
        )
        self.add_item(self.title_input)

        self.clan_field_title = ui.TextInput(
            label="Заголовок поля «Клан»",
            placeholder="🛡️ Набор в клан",
            default=config.get("panel_clan_field_title", ""),
            required=False, max_length=100, style=discord.TextStyle.short,
        )
        self.add_item(self.clan_field_title)

        self.clan_field_desc = ui.TextInput(
            label="Текст поля «Клан»",
            placeholder="Хочешь стать частью сильнейшего клана?...",
            default=config.get("panel_clan_field_desc", ""),
            required=False, max_length=1000, style=discord.TextStyle.paragraph,
        )
        self.add_item(self.clan_field_desc)

        self.mod_field_title = ui.TextInput(
            label="Заголовок поля «Модерация»",
            placeholder="👑 Набор в модерацию",
            default=config.get("panel_mod_field_title", ""),
            required=False, max_length=100, style=discord.TextStyle.short,
        )
        self.add_item(self.mod_field_title)

        self.mod_field_desc = ui.TextInput(
            label="Текст поля «Модерация»",
            placeholder="Готов поддерживать порядок и помогать клану расти?...",
            default=config.get("panel_mod_field_desc", ""),
            required=False, max_length=1000, style=discord.TextStyle.paragraph,
        )
        self.add_item(self.mod_field_desc)

    async def on_submit(self, interaction: discord.Interaction):
        # Сохраняем только непустые поля; пустые → удаляем (используем дефолт)
        def _save(key: str, value: str):
            v = value.strip()
            if v:
                self.config[key] = v
            else:
                self.config.pop(key, None)

        _save("panel_title", self.title_input.value)
        _save("panel_clan_field_title", self.clan_field_title.value)
        _save("panel_clan_field_desc", self.clan_field_desc.value)
        _save("panel_mod_field_title", self.mod_field_title.value)
        _save("panel_mod_field_desc", self.mod_field_desc.value)

        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return

        # Подсказка про вторую модалку
        await interaction.response.send_message(
            embed=build_success(
                title="✅ Тексты панели обновлены",
                description=(
                    "Заголовок, поля «Клан» и «Модерация» сохранены.\n\n"
                    "💡 Чтобы поменять также **«Как это работает»**, **футер** "
                    "и **опции dropdown** (эмодзи/лейблы) — откройте "
                    "`.editor` → **Внешний вид панели** ещё раз: теперь там "
                    "появится кнопка для второй части настроек."
                ),
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditPanelDropdownModal(ui.Modal):
    """Вторая модалка: опции dropdown'а (эмодзи, лейблы, descriptions,
    placeholder), поле «Как это работает», футер, иконка."""

    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="🎨 Внешний вид панели — опции и футер")
        self.config = config
        self.parent_view = parent_view

        self.select_placeholder = ui.TextInput(
            label="Placeholder dropdown'а",
            placeholder="🎫 Выберите категорию...",
            default=config.get("panel_select_placeholder", ""),
            required=False, max_length=100, style=discord.TextStyle.short,
        )
        self.add_item(self.select_placeholder)

        self.clan_option_label = ui.TextInput(
            label="Лейбл опции «Клан»",
            placeholder="Набор в клан",
            default=config.get("panel_clan_option_label", ""),
            required=False, max_length=100, style=discord.TextStyle.short,
        )
        self.add_item(self.clan_option_label)

        self.clan_option_emoji = ui.TextInput(
            label="Эмодзи опции «Клан» (1 символ)",
            placeholder="🛡️",
            default=config.get("panel_clan_option_emoji", ""),
            required=False, max_length=10, style=discord.TextStyle.short,
        )
        self.add_item(self.clan_option_emoji)

        self.mod_option_label = ui.TextInput(
            label="Лейбл опции «Модерация»",
            placeholder="Набор в модерацию",
            default=config.get("panel_mod_option_label", ""),
            required=False, max_length=100, style=discord.TextStyle.short,
        )
        self.add_item(self.mod_option_label)

        self.mod_option_emoji = ui.TextInput(
            label="Эмодзи опции «Модерация» (1 символ)",
            placeholder="👑",
            default=config.get("panel_mod_option_emoji", ""),
            required=False, max_length=10, style=discord.TextStyle.short,
        )
        self.add_item(self.mod_option_emoji)

    async def on_submit(self, interaction: discord.Interaction):
        def _save(key: str, value: str):
            v = value.strip()
            if v:
                self.config[key] = v
            else:
                self.config.pop(key, None)

        _save("panel_select_placeholder", self.select_placeholder.value)
        _save("panel_clan_option_label", self.clan_option_label.value)
        _save("panel_clan_option_emoji", self.clan_option_emoji.value)
        _save("panel_mod_option_label", self.mod_option_label.value)
        _save("panel_mod_option_emoji", self.mod_option_emoji.value)

        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=build_success(
                title="✅ Опции dropdown'а обновлены",
                description=(
                    f"**Placeholder:** {self.config.get('panel_select_placeholder') or '_(по умолчанию)_'}\n"
                    f"**Клан:** {self.config.get('panel_clan_option_emoji', '🛡️')} "
                    f"{self.config.get('panel_clan_option_label', 'Набор в клан')}\n"
                    f"**Модерация:** {self.config.get('panel_mod_option_emoji', '👑')} "
                    f"{self.config.get('panel_mod_option_label', 'Набор в модерацию')}\n\n"
                    "💡 Чтобы изменения появились на панели — пересоздайте её через "
                    "`.editor` → **♻️ Пересоздать панель**."
                ),
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditPanelExtrasModal(ui.Modal):
    """Третья модалка: поле «Как это работает», футер, иконка."""

    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="🎨 Панель — «Как это работает», футер, иконка")
        self.config = config
        self.parent_view = parent_view

        self.howto_field_title = ui.TextInput(
            label="Заголовок «Как это работает»",
            placeholder="📝 Как это работает",
            default=config.get("panel_howto_field_title", ""),
            required=False, max_length=100, style=discord.TextStyle.short,
        )
        self.add_item(self.howto_field_title)

        self.howto_field_desc = ui.TextInput(
            label="Текст «Как это работает»",
            placeholder="**1.** Выбери категорию в меню ниже\n**2.** Заполни анкету...",
            default=config.get("panel_howto_field_desc", ""),
            required=False, max_length=1000, style=discord.TextStyle.paragraph,
        )
        self.add_item(self.howto_field_desc)

        self.footer = ui.TextInput(
            label="Футер embed'а",
            placeholder="EGODiscord System • Выбери категорию в меню ниже ⬇️",
            default=config.get("panel_footer", ""),
            required=False, max_length=200, style=discord.TextStyle.short,
        )
        self.add_item(self.footer)

        self.thumbnail = ui.TextInput(
            label="URL иконки панели (или пусто = по умолчанию)",
            placeholder="https://cdn.discordapp.com/...",
            default=config.get("panel_thumbnail_url", ""),
            required=False, max_length=500, style=discord.TextStyle.short,
        )
        self.add_item(self.thumbnail)

    async def on_submit(self, interaction: discord.Interaction):
        def _save(key: str, value: str):
            v = value.strip()
            if v:
                self.config[key] = v
            else:
                self.config.pop(key, None)

        # Валидация URL иконки
        thumb = self.thumbnail.value.strip()
        if thumb and not thumb.startswith(("http://", "https://")):
            await interaction.response.send_message(
                embed=build_error(description="URL иконки должен начинаться с http:// или https://"),
                ephemeral=True,
            )
            return

        _save("panel_howto_field_title", self.howto_field_title.value)
        _save("panel_howto_field_desc", self.howto_field_desc.value)
        _save("panel_footer", self.footer.value)
        _save("panel_thumbnail_url", thumb)

        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=build_success(
                title="✅ Доп. настройки панели сохранены",
                description=(
                    "Поле «Как это работает», футер и иконка обновлены.\n\n"
                    "💡 Чтобы изменения появились — пересоздайте панель через "
                    "`.editor` → **♻️ Пересоздать панель**."
                ),
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditSteamKeyModal(ui.Modal):
    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="🔑 Steam API ключ")
        self.config = config
        self.parent_view = parent_view
        current = config.get("steam_api_key", "")
        self.key_input = ui.TextInput(
            label="Новый Steam API ключ",
            placeholder="Например: 7B429F2A48B86FA526DF37DA357C7A55",
            default=current,
            required=True,
            max_length=64,
            style=discord.TextStyle.short,
        )
        self.add_item(self.key_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_key = self.key_input.value.strip()
        if len(new_key) < 16:
            await interaction.response.send_message(
                embed=build_error(
                    description="Ключ слишком короткий. Проверьте, что вставили полный ключ."
                ),
                ephemeral=True,
            )
            return
        self.config["steam_api_key"] = new_key
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return
        masked = new_key[:6] + "…" + new_key[-4:]
        await interaction.response.send_message(
            embed=build_success(
                title="✅ Steam API ключ обновлён",
                description=f"Новый ключ: `{masked}`",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditChannelIdsModal(ui.Modal):
    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="📁 Каналы и категории")
        self.config = config
        self.parent_view = parent_view

        self.cat_clan = ui.TextInput(
            label="ID категории — Клан",
            default=str(config.get("category_clan_id", "")),
            required=True, max_length=20, style=discord.TextStyle.short,
        )
        self.cat_mod = ui.TextInput(
            label="ID категории — Модерация",
            default=str(config.get("category_mod_id", "")),
            required=True, max_length=20, style=discord.TextStyle.short,
        )
        self.log_ch = ui.TextInput(
            label="ID канала логов",
            default=str(config.get("log_channel_id", "")),
            required=True, max_length=20, style=discord.TextStyle.short,
        )
        self.accept_role = ui.TextInput(
            label="ID роли при принятии (EGO)",
            default=str(config.get("accept_role_id", "")),
            required=True, max_length=20, style=discord.TextStyle.short,
        )
        for inp in (self.cat_clan, self.cat_mod, self.log_ch, self.accept_role):
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.config["category_clan_id"] = int(self.cat_clan.value.strip())
            self.config["category_mod_id"] = int(self.cat_mod.value.strip())
            self.config["log_channel_id"] = int(self.log_ch.value.strip())
            self.config["accept_role_id"] = int(self.accept_role.value.strip())
        except ValueError:
            await interaction.response.send_message(
                embed=build_error(description="Все ID должны быть числами."),
                ephemeral=True,
            )
            return
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_success(
                title="✅ Каналы и роли обновлены",
                description="Новые ID применены мгновенно.",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditRolesModal(ui.Modal):
    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="👑 Роли персонала EGO")
        self.config = config
        self.parent_view = parent_view
        roles_cfg = config.get("roles", {})

        self.inputs: dict[str, ui.TextInput] = {}
        labels = {
            "leader": "ID роли — Лидер",
            "co_leader": "ID роли — Со-лидер",
            "administrator": "ID роли — Администратор",
            "moderator": "ID роли — Модератор",
            "helper": "ID роли — Хелпер",
        }
        for key, label in labels.items():
            inp = ui.TextInput(
                label=label,
                placeholder="0 — роль не задана",
                default=str(roles_cfg.get(key, 0)),
                required=False, max_length=20, style=discord.TextStyle.short,
            )
            self.inputs[key] = inp
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        roles_cfg = self.config.setdefault("roles", {})
        for key, inp in self.inputs.items():
            try:
                rid = int(inp.value.strip() or "0")
            except ValueError:
                rid = 0
            if rid > 0:
                roles_cfg[key] = rid
            else:
                roles_cfg.pop(key, None)
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_success(
                title="✅ Роли персонала обновлены",
                description="Новые роли применены мгновенно.",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditPingRolesModal(ui.Modal):
    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="🔔 Роли для пинга при создании тикета")
        self.config = config
        self.parent_view = parent_view
        clan_ping = config.get("ping_roles_clan", [])
        mod_ping = config.get("ping_roles_mod", [])

        # default может быть длиннее max_length=200, если config.json
        # отредактирован вручную с >10 ролями. Обрезаем, иначе send_modal
        # упадёт с HTTP 400.
        def _truncate_default(s: str, n: int = 195) -> str:
            return s[:n] + ("…" if len(s) > n else "")

        clan_default = ", ".join(str(r) for r in clan_ping) if clan_ping else ""
        mod_default = ", ".join(str(r) for r in mod_ping) if mod_ping else ""

        self.clan_input = ui.TextInput(
            label="Пинг при тикете Клан (через запятую)",
            default=_truncate_default(clan_default),
            required=False, max_length=200, style=discord.TextStyle.short,
        )
        self.mod_input = ui.TextInput(
            label="Пинг при тикете Модерация (через запятую)",
            default=_truncate_default(mod_default),
            required=False, max_length=200, style=discord.TextStyle.short,
        )
        self.add_item(self.clan_input)
        self.add_item(self.mod_input)

    async def on_submit(self, interaction: discord.Interaction):
        def parse(s: str) -> list[int]:
            out = []
            for part in s.replace(";", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    out.append(int(part))
                except ValueError:
                    continue
            return out

        self.config["ping_roles_clan"] = parse(self.clan_input.value)
        self.config["ping_roles_mod"] = parse(self.mod_input.value)
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_success(
                title="✅ Роли для пинга обновлены",
                description=(
                    f"**Клан:** {len(self.config['ping_roles_clan'])} ролей\n"
                    f"**Модерация:** {len(self.config['ping_roles_mod'])} ролей"
                ),
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditEmbedColorModal(ui.Modal):
    PRESET_COLORS = {
        "ego": ("EGO Фиолетовый", 0x5865F2),
        "red": ("EGO Красный", 0xED4245),
        "green": ("Успех Зелёный", 0x57F287),
        "gold": ("Золото", 0xF1C40F),
        "orange": ("Оранжевый", 0xE67E22),
        "pink": ("Розовый", 0xEB459E),
        "cyan": ("Голубой", 0x1ABC9C),
        "dark": ("Тёмный", 0x2C2F33),
    }

    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="🎨 Цвет embed-сообщений")
        self.config = config
        self.parent_view = parent_view
        current = config.get("embed_color", "5865F2")
        self.color_input = ui.TextInput(
            label="HEX цвет (без #) или ключевое слово",
            placeholder="5865F2 или ego / red / green / gold / pink",
            default=current,
            required=True, max_length=20, style=discord.TextStyle.short,
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.color_input.value.strip().lower().lstrip("#")
        if value in self.PRESET_COLORS:
            name, color_int = self.PRESET_COLORS[value]
            hex_str = f"{color_int:06X}"
        else:
            try:
                color_int = int(value, 16)
                if color_int < 0 or color_int > 0xFFFFFF:
                    raise ValueError("out of range")
                hex_str = f"{color_int:06X}"
                name = f"Пользовательский #{hex_str}"
            except ValueError:
                await interaction.response.send_message(
                    embed=build_error(
                        description=(
                            "Неверный формат. Используйте HEX без `#` (например `5865F2`) "
                            "или ключевое слово: ego, red, green, gold, orange, pink, cyan, dark"
                        ),
                    ),
                    ephemeral=True,
                )
                return

        self.config["embed_color"] = hex_str
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return

        preview_embed = discord.Embed(
            title="🎨 Цвет обновлён",
            description=(
                f"**Название:** {name}\n"
                f"**HEX:** `#{hex_str}`\n"
                f"**RGB:** `({(color_int >> 16) & 0xFF}, {(color_int >> 8) & 0xFF}, {color_int & 0xFF})`"
            ),
            color=color_int,
            timestamp=embeds.now_msk(),
        )
        preview_embed.set_footer(text="EGODiscord System • Editor")
        await interaction.response.send_message(embed=preview_embed, ephemeral=True)
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditWelcomeMessageModal(ui.Modal):
    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="👋 Приветствие в тикете")
        self.config = config
        self.parent_view = parent_view
        current = config.get("ticket_welcome_text", "")
        self.text_input = ui.TextInput(
            label="Текст приветствия (поддерживает Markdown)",
            placeholder="Привет, {user}! Твой тикет принят. Ожидай рекрутёра.",
            default=current,
            required=False, max_length=1500, style=discord.TextStyle.paragraph,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_text = self.text_input.value.strip()
        if new_text:
            self.config["ticket_welcome_text"] = new_text
        else:
            self.config.pop("ticket_welcome_text", None)
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_success(
                title="✅ Приветствие обновлено",
                description=f"Превью:\n\n> {_truncate(new_text or '_(по умолчанию)_', 800)}",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditBrandingModal(ui.Modal):
    def __init__(self, config: dict, parent_view: EditorDashboardView):
        super().__init__(title="🖼️ Брендинг (иконка)")
        self.config = config
        self.parent_view = parent_view
        current = config.get("brand_thumbnail_url", "")
        self.url_input = ui.TextInput(
            label="URL иконки (для embed'ов)",
            placeholder="https://cdn.discordapp.com/...",
            default=current,
            required=False, max_length=500, style=discord.TextStyle.short,
        )
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction):
        url = self.url_input.value.strip()
        if url and not url.startswith(("http://", "https://")):
            await interaction.response.send_message(
                embed=build_error(description="URL должен начинаться с http:// или https://"),
                ephemeral=True,
            )
            return
        if url:
            self.config["brand_thumbnail_url"] = url
        else:
            self.config.pop("brand_thumbnail_url", None)
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return
        embed = build_success(
            title="✅ Брендинг обновлён",
            description=f"Новая иконка:\n{url or '_(используется аватар бота)_'}",
        )
        if url:
            embed.set_thumbnail(url=url)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class ConfirmResetView(ui.View):
    def __init__(self, parent_view: EditorDashboardView):
        super().__init__(timeout=30)
        self.parent_view = parent_view

    @ui.button(label="Да, сбросить", emoji="⚠️",
               style=discord.ButtonStyle.danger, custom_id="ego_reset_confirm")
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        soft_keys = [
            "questions_clan", "questions_mod", "ticket_panel_text",
            "ticket_welcome_text", "embed_color", "brand_thumbnail_url",
            "ping_roles_clan", "ping_roles_mod",
        ]
        for k in soft_keys:
            self.parent_view.config.pop(k, None)
        if not _save_config(self.parent_view.config):
            await interaction.response.edit_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                view=None,
            )
            return
        await interaction.response.edit_message(
            embed=build_success(
                title="✅ Настройки сброшены",
                description="Сброшены: вопросы, текст, приветствие, цвет, брендинг, пинг-роли.",
            ),
            view=None,
        )
        try:
            await self.parent_view.message.edit(
                embed=_dashboard_embed(self.parent_view.config)
            )
        except (discord.HTTPException, AttributeError):
            pass

    @ui.button(label="Отмена", emoji="✖️",
               style=discord.ButtonStyle.secondary, custom_id="ego_reset_cancel")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            embed=build_info(description="Сброс отменён."),
            view=None,
        )


# ============================================================================
# Cog
# ============================================================================

class Editor(commands.Cog):
    """Интерактивный редактор настроек бота (dropdown-меню)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    @commands.command(name="editor", aliases=["edit", "настройки"])
    @commands.guild_only()
    async def editor_cmd(self, ctx: commands.Context, section: Optional[str] = None):
        """
        Открыть интерактивный дашборд настройки бота (через dropdown).

        Использование:
            .editor              — открыть главное меню
            .editor questions    — сразу открыть редактор вопросов клана
        """
        config = self._config()
        if not _is_admin(ctx.author, config):
            await ctx.send(embed=embeds.error_no_permission())
            return

        view = EditorDashboardView(config, ctx.author.id)
        msg = await ctx.send(embed=_dashboard_embed(config), view=view)
        view.message = msg

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Editor(bot))
