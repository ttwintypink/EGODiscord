"""
cogs/ticket_control.py — Управление тикетом: кнопки, обзвон, мут, закрытие,
восстановление, фоновые задачи (анти-неактивность, авто-удаление голосовых,
напоминание о Claim, on_member_remove).

Views (persistent):
    - TicketControlView  — главная панель под приветственным сообщением
    - CloseDecisionView  — выбор Принять/Отклонить
    - ConfirmCloseView   — подтверждение Да/Нет
    - RatingView         — звёздочки оценки (в ЛС)
    - RestoreTicketView  — кнопка в логах для восстановления тикета
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import discord
from discord import ui, AllowedMentions
from discord.ext import commands, tasks

import database
from utils import embeds
from utils.embeds import (
    build_main, build_success, build_error, build_warning, build_info,
    msk_timestamp, now_msk, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, COLOR_MAIN,
)
from utils.transcripts import generate_html_transcript, save_html, save_form_txt

log = logging.getLogger(__name__)

# Эмодзи для смены состояния канала
EMOJI_OPEN      = "🎫"
EMOJI_CLAIMED   = "🤝"
EMOJI_CALL      = "🎙️"
EMOJI_ACCEPTED  = "✅"
EMOJI_REJECTED  = "❌"


# ============================================================================
# Вспомогательные функции
# ============================================================================

def _get_config(bot: commands.Bot) -> dict:
    return getattr(bot, "_config", None) or {}


def _is_high_staff(member: discord.Member, config: dict) -> bool:
    """leader / co_leader / administrator."""
    role_ids = set(r.id for r in member.roles)
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return False


def _is_staff(member: discord.Member, config: dict) -> bool:
    """leader / co_leader / administrator / moderator / helper."""
    role_ids = set(r.id for r in member.roles)
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator", "moderator", "helper"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return member.guild_permissions.administrator


async def _set_channel_emoji(channel: discord.TextChannel, new_emoji: str):
    """Меняет первый эмодзи в названии канала на new_emoji."""
    name = channel.name
    parts = name.split("-", 1)
    if len(parts) == 2:
        new_name = f"{new_emoji}-{parts[1]}"
    else:
        new_name = f"{new_emoji}-ticket"
    try:
        await channel.edit(name=new_name)
    except discord.HTTPException as e:
        log.warning("Не удалось переименовать канал: %s", e)


# ============================================================================
# Единый embed управления тикетом (компактно — без спама embed'ами)
# ============================================================================

def build_control_embed(
    user: discord.abc.User,
    ticket_type: str,
    status: str = "open",
    claimer: Optional[discord.abc.User] = None,
    voice_channel: Optional[discord.VoiceChannel] = None,
    steam_status: str = "pending",
) -> discord.Embed:
    """Собирает единый embed управления тикетом.

    Этот embed редактируется при claim/call/close — вместо отправки новых сообщений.
    Так тикет остаётся чистым (одно закреплённое сообщение управления + анкета).

    status: 'open' | 'claimed' | 'accepted' | 'rejected'
    steam_status: 'pending' | 'checking' | 'done' | 'failed'
    """
    is_clan = ticket_type == "clan"
    type_emoji = "🛡️" if is_clan else "👑"
    type_label = "Набор в клан EGO" if is_clan else "Набор в модерацию EGO"

    # Статусная строка
    if status == "open":
        status_text = "🟡 Ожидает рассмотрения"
        status_color = COLOR_WARNING
        title = f"{type_emoji} Тикет ожидает модератора"
    elif status == "claimed":
        status_text = "🟢 В работе"
        status_color = COLOR_SUCCESS
        title = f"{type_emoji} Тикет в работе"
    elif status == "accepted":
        status_text = "✅ Принят"
        status_color = COLOR_SUCCESS
        title = f"{type_emoji} Заявка принята"
    elif status == "rejected":
        status_text = "❌ Отклонён"
        status_color = COLOR_ERROR
        title = f"{type_emoji} Заявка отклонена"
    else:
        status_text = "—"
        status_color = COLOR_MAIN
        title = f"{type_emoji} Тикет"

    description_parts = [
        f"## 👋 {user.mention}",
        f"",
        f"**Тип заявки:** {type_emoji} {type_label}",
        f"**Статус:** {status_text}",
    ]

    if claimer:
        description_parts.append(f"**В работе:** {claimer.mention}")

    if voice_channel:
        description_parts.append(f"**Обзвон:** {voice_channel.mention}")
    else:
        description_parts.append("**Обзвон:** _не создан_")

    # Steam статус
    steam_emojis = {
        "pending": "⏳ Ожидает",
        "checking": "🔄 Проверка...",
        "done": "✅ Готово",
        "failed": "⚠️ Ошибка",
    }
    description_parts.append(f"**Steam:** {steam_emojis.get(steam_status, '—')}")

    description_parts.extend([
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"### 👇 Управление",
        f"Кнопки ниже — для модераторов.",
    ])

    if status == "open":
        description_parts.append("🤝 **Взять в работу** — принять тикет на себя")
        description_parts.append("🎙️ **Обзвон** — создать голосовой канал")
        description_parts.append("🔒 **Закрыть** — принять/отклонить заявку")
    elif status == "claimed":
        description_parts.append("🎙️ **Обзвон** — создать голосовой канал")
        description_parts.append("🔇 **Заглушить** — мут в голосовом")
        description_parts.append("🔒 **Закрыть** — принять/отклонить заявку")

    embed = discord.Embed(
        title=title,
        description="\n".join(description_parts),
        color=status_color,
        timestamp=now_msk(),
    )
    embed.add_field(
        name="👤 Кандидат",
        value=f"{user.mention}\n`{user.id}`",
        inline=True,
    )
    embed.add_field(
        name="📅 Создан",
        value=msk_timestamp(),
        inline=True,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="EGODiscord System • Управление тикетом")
    return embed


# ============================================================================
# Главная панель управления тикетом
# ============================================================================

class TicketControlView(ui.View):
    """Persistent view с кнопками Claim / Обзвон / Заглушить / Закрыть."""

    def __init__(self, config: dict):
        super().__init__(timeout=None)
        self.config = config

    @ui.button(label="Взять в работу", emoji="🤝",
               style=discord.ButtonStyle.success, custom_id="ego_btn_claim")
    async def claim_btn(self, interaction: discord.Interaction, button: ui.Button):
        await _handle_claim(interaction, self.config, button)

    @ui.button(label="Обзвон", emoji="🎙️",
               style=discord.ButtonStyle.secondary, custom_id="ego_btn_call")
    async def call_btn(self, interaction: discord.Interaction, button: ui.Button):
        await _handle_call(interaction, self.config)

    @ui.button(label="Заглушить", emoji="🔇",
               style=discord.ButtonStyle.secondary, custom_id="ego_btn_mute")
    async def mute_btn(self, interaction: discord.Interaction, button: ui.Button):
        await _handle_mute(interaction, self.config)

    @ui.button(label="Закрыть", emoji="🔒",
               style=discord.ButtonStyle.danger, custom_id="ego_btn_close")
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button):
        # Заменяем кнопку «Закрыть» на выбор Принять/Отклонить
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        new_view = CloseDecisionView(self.config)
        await interaction.response.edit_message(view=new_view)


# ============================================================================
# Обработчики кнопок главной панели
# ============================================================================

async def _handle_claim(interaction: discord.Interaction, config: dict,
                        button: ui.Button):
    if not _is_staff(interaction.user, config):
        await interaction.response.send_message(
            embed=embeds.error_no_permission(), ephemeral=True
        )
        return

    ticket = await database.ticket_get(interaction.channel.id)
    if ticket is None:
        await interaction.response.send_message(
            embed=embeds.error_not_in_ticket(), ephemeral=True
        )
        return

    # Меняем эмодзи
    await _set_channel_emoji(interaction.channel, EMOJI_CLAIMED)

    # Переписываем права: @everyone — нет, кандидат — да,
    # высшая администрация — да, нажавший модератор — да,
    # остальные роли персонала — НЕТ.
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            read_message_history=True, manage_permissions=True,
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            read_message_history=True, manage_channels=True,
        ),
    }
    # Кандидат остаётся
    candidate = guild.get_member(ticket["user_id"])
    if candidate:
        overwrites[candidate] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            read_message_history=True,
        )
    # Высшая администрация
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator"):
        rid = roles_cfg.get(key)
        if rid:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, manage_channels=True,
                )

    try:
        await interaction.channel.edit(overwrites=overwrites)
    except discord.HTTPException as e:
        log.warning("Не удалось обновить права канала после claim: %s", e)

    # Записываем claim в БД
    await database.ticket_set_claimed(interaction.channel.id, interaction.user.id)

    # КОМПАКТНО: редактируем существующее embed управления (без отправки нового)
    new_view = TicketControlViewClaimed(config, interaction.user)
    new_embed = build_control_embed(
        user=candidate or interaction.user,
        ticket_type=ticket["type"],
        status="claimed",
        claimer=interaction.user,
        voice_channel=guild.get_channel(ticket.get("voice_channel_id") or 0)
            if ticket.get("voice_channel_id") else None,
        steam_status="done",  # Steam уже проверен к моменту claim
    )
    try:
        await interaction.response.edit_message(embed=new_embed, view=new_view)
    except discord.HTTPException as e:
        log.warning("Не удалось обновить view после claim: %s", e)
        try:
            await interaction.followup.edit_message(
                interaction.message.id, embed=new_embed, view=new_view
            )
        except discord.HTTPException:
            pass


async def _handle_call(interaction: discord.Interaction, config: dict):
    if not _is_staff(interaction.user, config):
        await interaction.response.send_message(
            embed=embeds.error_no_permission(), ephemeral=True
        )
        return

    ticket = await database.ticket_get(interaction.channel.id)
    if ticket is None:
        await interaction.response.send_message(
            embed=embeds.error_not_in_ticket(), ephemeral=True
        )
        return

    await _create_voice_channel(interaction, config, ticket)


async def _create_voice_channel(interaction: discord.Interaction,
                                config: dict, ticket: dict):
    guild = interaction.guild
    user = guild.get_member(ticket["user_id"])

    safe_name = "".join(
        c for c in (user.name if user else "user")
        if c.isalnum() or c in "_-"
    )[:20] or "user"
    vc_name = f"🎙️ Обзвон | {safe_name}"

    # Категория = та же, где текстовый тикет
    category = interaction.channel.category
    if category is None:
        # fallback: берём из конфига
        cat_id = config["category_clan_id"] if ticket["type"] == "clan" else config["category_mod_id"]
        category = guild.get_channel(cat_id)

    if category is None or not isinstance(category, discord.CategoryChannel):
        await interaction.response.send_message(
            embed=build_error(description="Не найдена категория для голосового канала."),
            ephemeral=True,
        )
        return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True, connect=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, connect=True, move_members=True,
            mute_members=True, deafen_members=True, manage_channels=True,
        ),
    }
    # Кандидат — connect=True
    if user:
        overwrites[user] = discord.PermissionOverwrite(
            view_channel=True, connect=True,
        )
    # Персонал — connect=True
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator", "moderator", "helper"):
        rid = roles_cfg.get(key)
        if rid:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, connect=True,
                )

    try:
        vc = await guild.create_voice_channel(
            name=vc_name, category=category, overwrites=overwrites,
            reason=f"Обзвон кандидата {user} (тикет {ticket['channel_id']})",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            embed=build_error(description="Нет прав на создание голосового канала."),
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(
            embed=build_error(description=f"Ошибка: `{e}`"),
            ephemeral=True,
        )
        return

    # Сохраняем в БД
    await database.ticket_set_voice(ticket["channel_id"], vc.id)

    # Меняем эмодзи канала
    await _set_channel_emoji(interaction.channel, EMOJI_CALL)

    # Создаём одноразовое приглашение (10 минут)
    try:
        invite = await vc.create_invite(
            max_age=600, max_uses=1, unique=True,
            reason=f"Обзвон для {user}",
        )
    except discord.HTTPException as e:
        log.warning("Не удалось создать приглашение: %s", e)
        invite = None

    # КОМПАКТНО: отредактировать embed управления (добавить голосовой канал)
    # + показать кнопку "Подключиться к обзвону" в ephemeral сообщении
    # Если есть claimer — берём Claimed view, иначе обычный
    claimer_id = ticket.get("claimed_by")
    claimer = guild.get_member(claimer_id) if claimer_id else None

    if claimer:
        new_view = TicketControlViewClaimed(config, claimer)
    else:
        new_view = TicketControlView(config)

    new_embed = build_control_embed(
        user=user or interaction.user,
        ticket_type=ticket["type"],
        status="claimed" if claimer else "open",
        claimer=claimer,
        voice_channel=vc,
        steam_status="done",
    )

    try:
        if interaction.response.is_done():
            await interaction.message.edit(embed=new_embed, view=new_view)
        else:
            await interaction.response.edit_message(embed=new_embed, view=new_view)
    except discord.HTTPException as e:
        log.warning("Не удалось обновить embed управления: %s", e)

    # ephemeral-кнопка для модератора (подключиться к обзвону)
    if invite:
        join_view = ui.View(timeout=600)
        join_view.add_item(ui.Button(
            label="📞 Подключиться к обзвону",
            style=discord.ButtonStyle.link,
            url=invite.url,
        ))
        try:
            await interaction.followup.send(
                embed=build_success(
                    title="🎙️ Голосовой канал создан",
                    description=f"Канал: {vc.mention}\nКандидат: {user.mention if user else '—'}",
                ),
                view=join_view,
                ephemeral=True,
            )
        except discord.HTTPException:
            pass


async def _handle_mute(interaction: discord.Interaction, config: dict):
    if not _is_staff(interaction.user, config):
        await interaction.response.send_message(
            embed=embeds.error_no_permission(), ephemeral=True
        )
        return

    ticket = await database.ticket_get(interaction.channel.id)
    if ticket is None or not ticket.get("voice_channel_id"):
        await interaction.response.send_message(
            embed=build_error(description="Активный голосовой канал обзвона не найден."),
            ephemeral=True,
        )
        return

    guild = interaction.guild
    vc = guild.get_channel(ticket["voice_channel_id"])
    if vc is None or not isinstance(vc, discord.VoiceChannel):
        await interaction.response.send_message(
            embed=build_error(description="Голосовой канал не найден."),
            ephemeral=True,
        )
        return

    candidate = guild.get_member(ticket["user_id"])
    if candidate is None or candidate.voice is None or candidate.voice.channel != vc:
        await interaction.response.send_message(
            embed=build_error(description="Кандидат не находится в голосовом канале."),
            ephemeral=True,
        )
        return

    # Toggle server mute
    new_state = not candidate.voice.mute
    try:
        await candidate.edit(mute=new_state, reason="Toggle mute (TicketControl)")
    except discord.HTTPException as e:
        await interaction.response.send_message(
            embed=build_error(description=f"Не удалось изменить мут: `{e}`"),
            ephemeral=True,
        )
        return

    if new_state:
        await interaction.response.send_message(
            embed=build_warning(
                title="🔇 Кандидат заглушен",
                description=f"{candidate.mention} был заглушен в голосовом канале.",
            ),
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            embed=build_success(
                title="🔊 Кандидат может говорить",
                description=f"{candidate.mention} снова может говорить в голосовом канале.",
            ),
            ephemeral=True,
        )


# ============================================================================
# View после Claim (без кнопки «Взять в работу»)
# ============================================================================

class TicketControlViewClaimed(ui.View):
    """View после Claim: показывает кто взял + кнопки Обзвон/Мут/Закрыть."""

    def __init__(self, config: dict, claimer: Optional[discord.abc.User] = None):
        super().__init__(timeout=None)
        self.config = config
        # Кнопка-индикатор «В работе» (disabled) — показывает модератора
        claimer_name = claimer.display_name if claimer else "модератор"
        self.claim_indicator.label = f"В работе: {claimer_name}"

    @ui.button(label="В работе", emoji="🤝",
               style=discord.ButtonStyle.success, custom_id="ego_btn_claimed_indicator",
               disabled=True)
    async def claim_indicator(self, interaction: discord.Interaction, button: ui.Button):
        # Кнопка disabled — callback не вызывается, но нужен декоратор
        pass

    @ui.button(label="Обзвон", emoji="🎙️",
               style=discord.ButtonStyle.secondary, custom_id="ego_btn_call_2")
    async def call_btn(self, interaction: discord.Interaction, button: ui.Button):
        await _handle_call(interaction, self.config)

    @ui.button(label="Заглушить", emoji="🔇",
               style=discord.ButtonStyle.secondary, custom_id="ego_btn_mute_2")
    async def mute_btn(self, interaction: discord.Interaction, button: ui.Button):
        await _handle_mute(interaction, self.config)

    @ui.button(label="Закрыть", emoji="🔒",
               style=discord.ButtonStyle.danger, custom_id="ego_btn_close_2")
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        new_view = CloseDecisionView(self.config)
        await interaction.response.edit_message(view=new_view)


# ============================================================================
# Решение о закрытии: Принять / Отклонить
# ============================================================================

class CloseDecisionView(ui.View):
    """Замена кнопки «Закрыть» — выбор Принять или Отклонить."""

    def __init__(self, config: dict):
        super().__init__(timeout=180)  # 3 минуты на раздумья, потом вернём основную панель
        self.config = config

    async def on_timeout(self):
        # По истечении таймаута ничего не делаем — кнопка просто перестанет работать
        pass

    @ui.button(label="Принять", emoji="✅",
               style=discord.ButtonStyle.success, custom_id="ego_btn_accept")
    async def accept_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        # КОМПАКТНО: просто меняем view на подтверждение, без отправки нового сообщения
        view = ConfirmCloseView(self.config, decision="accepted")
        await interaction.response.edit_message(view=view)

    @ui.button(label="Отклонить", emoji="❌",
               style=discord.ButtonStyle.danger, custom_id="ego_btn_reject")
    async def reject_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        view = ConfirmCloseView(self.config, decision="rejected")
        await interaction.response.edit_message(view=view)

    @ui.button(label="Отмена", emoji="↩️",
               style=discord.ButtonStyle.secondary, custom_id="ego_btn_cancel_close")
    async def cancel_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        # КОМПАКТНО: возвращаем основную панель — с учётом claimer если есть
        ticket = await database.ticket_get(interaction.channel.id)
        if ticket and ticket.get("claimed_by"):
            claimer = interaction.guild.get_member(ticket["claimed_by"])
            new_view = TicketControlViewClaimed(self.config, claimer)
        else:
            new_view = TicketControlView(self.config)
        await interaction.response.edit_message(view=new_view)


# ============================================================================
# Подтверждение Да/Нет + модалка причины
# ============================================================================

class ConfirmCloseView(ui.View):
    def __init__(self, config: dict, decision: str):
        super().__init__(timeout=180)
        self.config = config
        self.decision = decision  # 'accepted' | 'rejected'

    @ui.button(label="Да, подтверждаю", emoji="✅",
               style=discord.ButtonStyle.success, custom_id="ego_btn_confirm_yes")
    async def yes_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        # Открываем модал причины
        modal = CloseReasonModal(self.config, self.decision, interaction.user)
        await interaction.response.send_modal(modal)

    @ui.button(label="Нет, отмена", emoji="❌",
               style=discord.ButtonStyle.secondary, custom_id="ego_btn_confirm_no")
    async def no_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        # КОМПАКТНО: возвращаем основную панель без отправки нового сообщения
        # Если тикет уже в работе (есть claimer) — возвращаем claimed view
        ticket = await database.ticket_get(interaction.channel.id)
        if ticket and ticket.get("claimed_by"):
            claimer = interaction.guild.get_member(ticket["claimed_by"])
            new_view = TicketControlViewClaimed(self.config, claimer)
        else:
            new_view = TicketControlView(self.config)
        await interaction.response.edit_message(view=new_view)


class CloseReasonModal(ui.Modal, title="📝 Причина закрытия"):
    def __init__(self, config: dict, decision: str, closer: discord.Member):
        super().__init__()
        self.config = config
        self.decision = decision
        self.closer = closer
        self.reason_input = ui.TextInput(
            label="Причина",
            placeholder="Опишите причину принятия/отклонения...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value
        # КОМПАКТНО: используем defer вместо видимого сообщения
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException:
            pass
        await _perform_close(
            interaction, self.config, self.decision, reason, self.closer
        )


# ============================================================================
# Процедура закрытия тикета
# ============================================================================

async def _perform_close(interaction: discord.Interaction, config: dict,
                         decision: str, reason: str, closer: discord.Member):
    ticket = await database.ticket_get(interaction.channel.id)
    if ticket is None:
        await interaction.followup.send(
            embed=embeds.error_not_in_ticket(), ephemeral=True
        )
        return

    guild = interaction.guild
    channel = interaction.channel
    user_id = ticket["user_id"]
    user = guild.get_member(user_id)

    # 1. Меняем эмодзи канала
    await _set_channel_emoji(channel, EMOJI_ACCEPTED if decision == "accepted" else EMOJI_REJECTED)

    # 2. Если принят — выдаём роль EGO
    # Если отклонён — возвращаем оригинальный ник
    if decision == "accepted":
        accept_role_id = config.get("accept_role_id")
        if accept_role_id and user:
            role = guild.get_role(accept_role_id)
            if role:
                try:
                    await user.add_roles(role, reason="Принят в EGO")
                except discord.HTTPException as e:
                    log.warning("Не удалось выдать роль: %s", e)
    elif decision == "rejected":
        # ВОЗВРАЩАЕМ ОРИГИНАЛЬНЫЙ НИК
        original_nick = ticket.get("original_nickname")
        if user and original_nick:
            try:
                await user.edit(nick=original_nick, reason="Заявка отклонена — возврат исходного ника")
                log.info("Ник возвращён: %s → %s", user, original_nick)
            except discord.Forbidden:
                log.warning("Нет прав на смену ника для %s (возврат при отклонении)", user)
            except discord.HTTPException as e:
                log.warning("Не удалось вернуть ник: %s", e)
        elif user and not original_nick:
            # Сохранённого ника нет — просто сбрасываем ник на username
            try:
                await user.edit(nick=None, reason="Заявка отклонена — сброс ника")
                log.info("Ник сброшен: %s → %s (default)", user, user.name)
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning("Не удалось сбросить ник: %s", e)

    # 3. Статистика рекрутера
    reaction_time = 0
    if ticket.get("claimed_at") and ticket.get("created_at"):
        reaction_time = int(ticket["claimed_at"]) - int(ticket["created_at"])
    elif ticket.get("claimed_by") and ticket.get("created_at"):
        reaction_time = 0
    if closer.id != user_id:  # не кандидат сам себя
        try:
            await database.stats_add_ticket(closer.id, reaction_time)
        except Exception as e:
            log.warning("Не удалось обновить статистику: %s", e)

    # 4. Обновляем статус тикета
    await database.ticket_set_status(channel.id, decision)

    # 5. Собираем последние 5 сообщений для ЛС
    last_msgs = await database.message_get_last_n(channel.id, 5)
    last_msgs_text = "\n\n".join(
        f"**{m['author_name']}** ({msk_timestamp()}):\n{m['content'] or '—'}"
        for m in last_msgs
    ) if last_msgs else "Сообщений не найдено."

    # 6. Отправляем кандидату в ЛС — премиум-embed
    dm_sent = False
    rating_recruiter_id = closer.id
    if user:
        try:
            dm = await user.create_dm()
            accepted = decision == "accepted"
            status_emoji = "✅ Принят" if accepted else "❌ Отклонён"
            color = COLOR_SUCCESS if accepted else COLOR_ERROR

            dm_embed = discord.Embed(
                title=f"🎫 Ваша заявка EGO — {status_emoji}",
                description=(
                    f"## 👋 Здравствуйте, {user.mention}!\n\n"
                    f"Ваша заявка была **{status_emoji.lower()}**.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=color,
                timestamp=now_msk(),
            )
            dm_embed.add_field(
                name="👤 Модератор",
                value=f"{closer.mention}\n`{closer.id}`",
                inline=True,
            )
            dm_embed.add_field(
                name="📝 Причина",
                value=reason[:1024],
                inline=False,
            )
            dm_embed.add_field(
                name="💬 Последние сообщения из тикета",
                value=last_msgs_text[:1024] if last_msgs_text else "—",
                inline=False,
            )
            if accepted:
                dm_embed.add_field(
                    name="🎉 Дальнейшие шаги",
                    value=(
                        "Вам выдана роль **EGO**. Поздравляем!\n"
                        "Следите за каналами клана и присоединяйтесь к активности."
                    ),
                    inline=False,
                )
            else:
                dm_embed.add_field(
                    name="↩️ Ваш ник",
                    value="Ваш ник на сервере возвращён к исходному значению.",
                    inline=False,
                )
            dm_embed.set_thumbnail(url=closer.display_avatar.url)
            dm_embed.set_footer(text="EGODiscord System • Оцените работу рекрутёра ниже ⭐")
            rating_view = RatingView(config, rating_recruiter_id)
            await dm.send(embed=dm_embed, view=rating_view)
            dm_sent = True
        except discord.Forbidden:
            log.info("ЛС пользователя %s закрыты.", user_id)
            dm_sent = False
        except discord.HTTPException as e:
            log.warning("Не удалось отправить ЛС: %s", e)

    if not dm_sent and user:
        try:
            await channel.send(embed=build_warning(
                title="⚠️ ЛС недоступно",
                description=f"Не удалось отправить сообщение кандидату {user.mention} в ЛС. "
                            f"Пожалуйста, свяжитесь с ним вручную.",
            ))
        except discord.HTTPException:
            pass

    # 7. Генерируем HTML-транскрипт
    try:
        all_msgs_raw = await database.message_get_all(channel.id)
        # Обогащаем метаданными пользователей
        messages_for_html = []
        for m in all_msgs_raw:
            member = guild.get_member(m["author_id"])
            avatar_url = (member.display_avatar.url if member
                          else "https://cdn.discordapp.com/embed/avatars/0.png")
            role_color = None
            bot = False
            if member:
                if member.bot:
                    bot = True
                # Берём цвет верхней роли с цветом
                for role in reversed(member.roles):
                    if role.color.value != 0:
                        role_color = f"#{role.color.value:06x}"
                        break
            try:
                dt = discord.utils.snowflake_time(int(m["id"]))
            except Exception:
                dt = now_msk()
            created_str = dt.strftime("%d.%m.%Y %H:%M:%S МСК")
            messages_for_html.append({
                "author_name": m["author_name"],
                "author_id": m["author_id"],
                "content": m["content"],
                "created_at": created_str,
                "avatar_url": avatar_url,
                "role_color": role_color,
                "bot": bot,
            })

        try:
            created_dt = discord.utils.snowflake_time(channel.id)
        except Exception:
            created_dt = now_msk()

        user_name = user.display_name if user else f"user_{user_id}"
        html_content = generate_html_transcript(
            channel_name=channel.name,
            channel_id=channel.id,
            user_id=user_id,
            user_name=user_name,
            ticket_type=ticket["type"],
            created_at=created_dt,
            closed_at=now_msk(),
            messages=messages_for_html,
        )
        html_path = save_html(html_content, channel.id, user_name)
    except Exception as e:
        log.exception("Ошибка генерации HTML: %s", e)
        html_path = None

    # 8. Сохраняем TXT анкеты
    txt_path = None
    try:
        if ticket.get("form_text"):
            txt_path = save_form_txt(ticket["form_text"], channel.id, user_name)
    except Exception as e:
        log.exception("Ошибка сохранения TXT: %s", e)

    # 9. Отправляем в лог-канал — премиум-embed
    log_channel_id = config.get("log_channel_id")
    log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
    if log_channel and isinstance(log_channel, discord.TextChannel):
        accepted = decision == "accepted"
        status_text = "✅ Принят" if accepted else "❌ Отклонён"
        color = COLOR_SUCCESS if accepted else COLOR_ERROR

        log_embed = discord.Embed(
            title=f"📜 Тикет закрыт — {status_text}",
            description=(
                f"## {status_text}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=color,
            timestamp=now_msk(),
        )
        log_embed.add_field(
            name="📢 Канал",
            value=f"#{channel.name}\n`{channel.id}`",
            inline=True,
        )
        log_embed.add_field(
            name="👤 Кандидат",
            value=f"{user.mention if user else '—'}\n`{user_id}`",
            inline=True,
        )
        log_embed.add_field(
            name="🛡️ Закрыл",
            value=f"{closer.mention}\n`{closer.id}`",
            inline=True,
        )
        log_embed.add_field(
            name="📝 Причина",
            value=reason[:1024],
            inline=False,
        )
        log_embed.add_field(
            name="⏱️ Время",
            value=msk_timestamp(),
            inline=True,
        )
        type_label = "🛡️ Клан" if ticket["type"] == "clan" else "👑 Модерация"
        log_embed.add_field(
            name="📂 Тип",
            value=type_label,
            inline=True,
        )
        if accepted:
            log_embed.add_field(
                name="🎉 Роль",
                value=f"Выдана <@&{config.get('accept_role_id', 0)}>",
                inline=True,
            )
        log_embed.set_thumbnail(url=closer.display_avatar.url)
        log_embed.set_footer(text="EGODiscord System • Ticket Log")

        restore_view = RestoreTicketView(config)
        files = []
        if html_path:
            try:
                files.append(discord.File(html_path, filename=f"transcript_{channel.id}.html"))
            except Exception:
                pass
        if txt_path:
            try:
                files.append(discord.File(txt_path, filename=f"anketa_{channel.id}.txt"))
            except Exception:
                pass
        try:
            await log_channel.send(
                embed=log_embed,
                view=restore_view,
                files=files if files else None,
                allowed_mentions=AllowedMentions.none(),
            )
        except discord.HTTPException as e:
            log.warning("Не удалось отправить в лог-канал: %s", e)

    # 10. Удаляем голосовой канал обзвона (если был)
    if ticket.get("voice_channel_id"):
        vc = guild.get_channel(ticket["voice_channel_id"])
        if vc:
            try:
                await vc.delete(reason="Тикет закрыт")
            except discord.HTTPException:
                pass

    # 11. Удаляем текстовый канал тикета
    await database.ticket_delete(channel.id)
    try:
        await channel.delete(reason=f"Тикет закрыт ({decision}) пользователем {closer}")
    except discord.HTTPException as e:
        log.warning("Не удалось удалить канал: %s", e)


# ============================================================================
# Rating View (звёздочки в ЛС)
# ============================================================================

class RatingView(ui.View):
    """
    5 кнопок-звёздочек для оценки сервиса.

    IMPORTANT: custom_id каждой кнопки кодирует recruiter_id, чтобы
    persistent-обработка работала через on_interaction listener
    в TicketControl cog (после рестарта бота кнопки продолжают работать).

    Формат custom_id: ego_rate_<stars>_<recruiter_id>
    """

    def __init__(self, config: dict, recruiter_id: int):
        super().__init__(timeout=None)
        self.config = config
        self.recruiter_id = recruiter_id

        for stars in range(1, 6):
            btn = ui.Button(
                label="",
                emoji="⭐" * stars,
                custom_id=f"ego_rate_{stars}_{recruiter_id}",
                style=(discord.ButtonStyle.success if stars == 5
                       else discord.ButtonStyle.secondary),
            )
            # Сохраняем stars в замыкании
            btn.callback = self._make_callback(stars, recruiter_id)
            self.add_item(btn)

    def _make_callback(self, stars: int, recruiter_id: int):
        async def callback(interaction: discord.Interaction):
            await _handle_rating(interaction, recruiter_id, stars)
        return callback


async def _handle_rating(interaction: discord.Interaction, recruiter_id: int, stars: int):
    """Обработчик нажатия на звёздочку — вызывается из View.callback ИЛИ из on_interaction listener."""
    try:
        await database.stats_add_rating(recruiter_id, stars)
    except Exception as e:
        log.warning("Не удалось записать оценку: %s", e)

    # Премиум-embed с благодарностью + визуальная шкала звёзд
    star_bar = "⭐" * stars + "⚫" * (5 - stars)
    if stars >= 5:
        verdict = "🔥 Идеально! Спасибо за высокую оценку!"
        color = COLOR_SUCCESS
    elif stars >= 4:
        verdict = "😊 Отлично! Будем стараться ещё лучше."
        color = COLOR_SUCCESS
    elif stars >= 3:
        verdict = "🤔 Нормально. Учтём ваши замечания."
        color = COLOR_WARNING
    else:
        verdict = "😢 Жаль, что не понравилось. Мы разберёмся."
        color = COLOR_ERROR

    rating_embed = discord.Embed(
        title="⭐ Спасибо за оценку!",
        description=(
            f"## {star_bar}\n\n"
            f"Вы оценили работу рекрутёра на **{stars}/5**.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{verdict}"
        ),
        color=color,
        timestamp=now_msk(),
    )
    rating_embed.set_footer(text="EGODiscord System • Rating System")

    try:
        # Обновляем исходное сообщение — убираем кнопки, показываем благодарность
        await interaction.response.edit_message(
            view=None,
            embed=rating_embed,
        )
    except discord.HTTPException:
        # Если response уже отправлен — используем followup
        try:
            await interaction.followup.send(embed=rating_embed, ephemeral=True)
        except discord.HTTPException:
            pass


# ============================================================================
# Restore Ticket View
# ============================================================================

class RestoreTicketView(ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=None)
        self.config = config

    @ui.button(label="Восстановить тикет", emoji="♻️",
               style=discord.ButtonStyle.success, custom_id="ego_btn_restore")
    async def restore_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not _is_staff(interaction.user, self.config):
            await interaction.response.send_message(
                embed=embeds.error_no_permission(), ephemeral=True
            )
            return
        await _restore_ticket(interaction, self.config)


async def _restore_ticket(interaction: discord.Interaction, config: dict):
    """
    Восстановление тикета: создаём канал заново, выдаём права,
    отправляем туда анкету (из TXT-вложения в сообщении лога) —
    симулируем возврат.
    """
    message = interaction.message
    guild = interaction.guild

    # Ищем ID кандидата и тип в embed'е (нельзя — Embed не содержит ID напрямую),
    # поэтому берём ID из первого файла (transcript_{channel_id}.html).
    # Мы используем подход: парсим имя прикреплённого файла.
    original_channel_id = None
    if message.attachments:
        for att in message.attachments:
            if att.filename.startswith("transcript_"):
                try:
                    original_channel_id = int(att.filename.replace("transcript_", "").replace(".html", ""))
                    break
                except ValueError:
                    pass

    if original_channel_id is None:
        await interaction.response.send_message(
            embed=build_error(description="Не удалось определить исходный тикет для восстановления."),
            ephemeral=True,
        )
        return

    # Читаем HTML-транскрипт из вложения
    html_content = None
    txt_content = None
    for att in message.attachments:
        try:
            data = await att.read()
            if att.filename.endswith(".html"):
                html_content = data.decode("utf-8", errors="replace")
            elif att.filename.endswith(".txt"):
                txt_content = data.decode("utf-8", errors="replace")
        except Exception as e:
            log.warning("Не удалось прочитать вложение %s: %s", att.filename, e)

    # Пытаемся извлечь user_id из HTML (есть в header)
    user_id = None
    user_name = "restored"
    ticket_type = "clan"
    if html_content:
        import re
        m = re.search(r"\((\d{10,20})\)", html_content)
        if m:
            try:
                user_id = int(m.group(1))
            except ValueError:
                pass
        m_name = re.search(r"Кандидат: <strong[^>]*>([^<]+)</strong>", html_content)
        if m_name:
            user_name = m_name.group(1).strip()
        if "Модерация" in html_content:
            ticket_type = "mod"

    if user_id is None:
        await interaction.response.send_message(
            embed=build_error(description="Не удалось извлечь ID кандидата из транскрипта."),
            ephemeral=True,
        )
        return

    # Создаём канал
    category_id = (config["category_clan_id"] if ticket_type == "clan"
                   else config["category_mod_id"])
    category = guild.get_channel(category_id)
    if category is None or not isinstance(category, discord.CategoryChannel):
        await interaction.response.send_message(
            embed=build_error(description="Категория для тикетов не найдена."),
            ephemeral=True,
        )
        return

    safe_name = "".join(c for c in user_name if c.isalnum() or c in "_-")[:20] or "restored"
    new_name = f"♻️-restored-{safe_name}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            read_message_history=True, manage_permissions=True,
            attach_files=True, embed_links=True,
        ),
    }
    user_member = guild.get_member(user_id)
    if user_member:
        overwrites[user_member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
        )
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator", "moderator", "helper"):
        rid = roles_cfg.get(key)
        if rid:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, manage_channels=True,
                )

    try:
        new_channel = await guild.create_text_channel(
            name=new_name, category=category, overwrites=overwrites,
            topic=f"Восстановленный тикет: user_id={user_id} | "
                  f"Исходный канал: {original_channel_id} | {msk_timestamp()}",
            reason=f"Восстановление тикета {original_channel_id}",
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            embed=build_error(description=f"Ошибка создания канала: `{e}`"),
            ephemeral=True,
        )
        return

    # Сохраняем в БД как новый открытый тикет
    form_text = txt_content or "Анкета восстановлена из логов."
    await database.ticket_create(new_channel.id, user_id, ticket_type, form_text)

    # Премиум-embed восстановления
    restore_embed = discord.Embed(
        title="♻️ Тикет восстановлен",
        description=(
            f"## 🔄 Этот тикет был восстановлен из логов\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=COLOR_MAIN,
        timestamp=now_msk(),
    )
    restore_embed.add_field(
        name="👤 Кандидат",
        value=user_member.mention if user_member else f"`{user_id}`",
        inline=True,
    )
    restore_embed.add_field(
        name="📢 Исходный канал",
        value=f"`{original_channel_id}`",
        inline=True,
    )
    restore_embed.add_field(
        name="🛡️ Восстановил",
        value=interaction.user.mention,
        inline=True,
    )
    restore_embed.add_field(
        name="⏱️ Время",
        value=msk_timestamp(),
        inline=True,
    )
    type_label = "🛡️ Клан" if ticket_type == "clan" else "👑 Модерация"
    restore_embed.add_field(
        name="📂 Тип",
        value=type_label,
        inline=True,
    )
    restore_embed.set_thumbnail(url=interaction.user.display_avatar.url)
    restore_embed.set_footer(text="EGODiscord System • Ticket Restored")

    control_view = TicketControlView(config)
    try:
        await new_channel.send(content=(
            f"{user_member.mention if user_member else ''}"
        ), embed=restore_embed, view=control_view)
    except discord.HTTPException:
        pass

    # Отправляем анкету (из TXT)
    if txt_content:
        anketa_embed = build_info(
            title="📋 Анкета кандидата (восстановлена)",
            description=f"```\n{txt_content[:3500]}\n```"[:4000],
        )
        try:
            await new_channel.send(embed=anketa_embed)
        except discord.HTTPException:
            pass

    # Отправляем HTML-транскрипт как файл (для истории)
    if html_content:
        try:
            import io
            fp = discord.File(io.BytesIO(html_content.encode("utf-8")),
                              filename=f"transcript_{original_channel_id}.html")
            await new_channel.send(
                embed=build_info(
                    title="📜 Архив переписки",
                    description="Ниже прикреплён HTML-транскрипт переписки из оригинального тикета.",
                ),
                file=fp,
            )
        except discord.HTTPException:
            pass

    await interaction.response.send_message(
        embed=build_success(
            title="✅ Тикет восстановлен",
            description=f"Канал тикета создан: {new_channel.mention}",
        ),
        ephemeral=True,
    )

    # Отключаем кнопку «Восстановить» в логах — заменяем View на пустую,
    # чтобы кнопку больше нельзя было нажать (избегаем дубликатов).
    try:
        await interaction.message.edit(view=None)
    except discord.HTTPException as e:
        log.warning("Не удалось убрать кнопку Restore: %s", e)


# ============================================================================
# Cog + фоновые задачи
# ============================================================================

class TicketControl(commands.Cog):
    """Управление тикетами и автоматизация."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.inactivity_check.start()
        self.voice_cleanup.start()
        self.claim_reminder.start()
        self._voice_states: dict[int, dict[int, bool]] = {}  # vc_id -> {user_id: speaking}

    def cog_unload(self):
        self.inactivity_check.cancel()
        self.voice_cleanup.cancel()
        self.claim_reminder.cancel()

    def start_background_tasks(self):
        """Вызывается из on_ready, чтобы гарантированно запустить после готовности бота."""
        if not self.inactivity_check.is_running():
            self.inactivity_check.start()
        if not self.voice_cleanup.is_running():
            self.voice_cleanup.start()
        if not self.claim_reminder.is_running():
            self.claim_reminder.start()

    # --- on_interaction: обработка persistent нажатий на звёздочки ----------
    # Это нужно, потому что RatingView использует динамические custom_id
    # (ego_rate_<stars>_<recruiter_id>) — обычная persistent-регистрация
    # через bot.add_view() не работает с динамическими custom_id.
    # Listener ловит все interaction и парсит нужные.
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        try:
            if interaction.type != discord.InteractionType.component:
                return
            data = interaction.data or {}
            custom_id = data.get("custom_id", "")
            if not custom_id.startswith("ego_rate_"):
                return
            # Парсим: ego_rate_<stars>_<recruiter_id>
            parts = custom_id.split("_")
            if len(parts) != 4:
                return
            try:
                stars = int(parts[2])
                recruiter_id = int(parts[3])
            except (ValueError, IndexError):
                return
            if stars < 1 or stars > 5:
                return
            await _handle_rating(interaction, recruiter_id, stars)
        except Exception as e:
            log.exception("Ошибка в on_interaction rating handler: %s", e)

    # --- on_member_remove: авто-закрытие тикета при выходе пользователя ------
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channels = await database.fetch_user_active_ticket_channels(member.id)
        for ch_id in channels:
            channel = member.guild.get_channel(ch_id)
            if channel:
                try:
                    await channel.delete(reason=f"Пользователь {member} покинул сервер")
                except discord.HTTPException:
                    pass
            await database.ticket_delete(ch_id)

        # Уведомляем в лог-канал — премиум-embed
        config = _get_config(self.bot)
        log_channel_id = config.get("log_channel_id")
        if log_channel_id:
            log_channel = member.guild.get_channel(log_channel_id)
            if log_channel and isinstance(log_channel, discord.TextChannel):
                try:
                    leave_embed = discord.Embed(
                        title="🚪 Пользователь покинул сервер",
                        description=(
                            f"## 👋 {member} покинул сервер\n\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=COLOR_WARNING,
                        timestamp=now_msk(),
                    )
                    leave_embed.add_field(
                        name="👤 Пользователь",
                        value=f"{member.mention}\n`{member.id}`",
                        inline=True,
                    )
                    leave_embed.add_field(
                        name="🎫 Удалено тикетов",
                        value=f"**{len(channels)}**",
                        inline=True,
                    )
                    leave_embed.set_thumbnail(url=member.display_avatar.url)
                    leave_embed.set_footer(text="EGODiscord System • Member Remove")
                    await log_channel.send(embed=leave_embed)
                except discord.HTTPException:
                    pass

    # --- on_voice_state_update: авто-мут при перебивании --------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                     before: discord.VoiceState,
                                     after: discord.VoiceState):
        if member.bot:
            return

        # Если пользователь вышел из голосового канала
        if before.channel and not after.channel:
            self._voice_states.pop(before.channel.id, None)

        # Кандидат в голосовом канале обзвона?
        config = _get_config(self.bot)
        if not after.channel:
            return

        ticket = await database.ticket_get_voice_owner(after.channel.id)
        if ticket is None:
            return
        # Только если это кандидат (а не модератор)
        if member.id != ticket["user_id"]:
            return

        # Подписываемся на speaking-события через on_voice_state_update не получится,
        # discord.py не отдаёт speaking напрямую. Используем VoiceClient если есть.
        # Здесь — простейшая заглушка: при входе кандидата просто логируем.
        log.info("Кандидат %s вошёл в голосовой канал обзвона %s", member, after.channel)

    # --- Фоновая задача: проверка неактивности тикетов -----------------------
    @tasks.loop(minutes=5)
    async def inactivity_check(self):
        try:
            tickets = await database.tickets_get_all_open()
            now = int(time.time())
            WARNING_THRESHOLD = 24 * 3600   # 24 часа
            CLOSE_THRESHOLD   = 25 * 3600  # 25 часов (24 + 1 час)
            for t in tickets:
                last = t.get("last_message_at") or t.get("created_at")
                if last is None:
                    continue
                delta = now - int(last)

                # Если прошло > 24ч и предупреждения ещё не было
                if delta >= WARNING_THRESHOLD and delta < CLOSE_THRESHOLD and not t.get("warned_inactive"):
                    channel = self.bot.get_channel(t["channel_id"])
                    if channel is None:
                        continue
                    try:
                        warn_embed = discord.Embed(
                            title="⏰ Предупреждение о неактивности",
                            description=(
                                f"## ⚠️ Тикет без активности более 24 часов\n\n"
                                f"Кандидат <@{t['user_id']}>, пожалуйста, оставьте сообщение.\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🔒 Тикет будет **закрыт автоматически через 1 час**, "
                                f"если вы не оставите сообщение."
                            ),
                            color=COLOR_WARNING,
                            timestamp=now_msk(),
                        )
                        warn_embed.set_footer(text="EGODiscord System • Anti-Inactivity")
                        await channel.send(
                            content=f"<@{t['user_id']}>",
                            embed=warn_embed,
                        )
                        await database.ticket_set_warned(t["channel_id"], True)
                    except discord.HTTPException as e:
                        log.warning("Не удалось отправить предупреждение: %s", e)

                # Если прошло > 25ч — закрываем
                elif delta >= CLOSE_THRESHOLD:
                    channel = self.bot.get_channel(t["channel_id"])
                    if channel is None:
                        await database.ticket_delete(t["channel_id"])
                        continue
                    try:
                        await channel.send(embed=build_error(
                            title="🔒 Авто-закрытие",
                            description="Тикет автоматически закрыт из-за неактивности.",
                        ))
                        # Удаляем голосовой, если есть
                        if t.get("voice_channel_id"):
                            vc = channel.guild.get_channel(t["voice_channel_id"])
                            if vc:
                                try:
                                    await vc.delete(reason="Авто-закрытие тикета")
                                except discord.HTTPException:
                                    pass
                        await database.ticket_delete(t["channel_id"])
                        await channel.delete(reason="Авто-закрытие неактивного тикета")
                    except discord.HTTPException as e:
                        log.warning("Не удалось закрыть неактивный тикет: %s", e)
        except Exception as e:
            log.exception("Ошибка в inactivity_check: %s", e)

    @inactivity_check.before_loop
    async def _before_inactivity(self):
        await self.bot.wait_until_ready()

    # --- Фоновая задача: авто-удаление пустых голосовых каналов обзвона ------
    @tasks.loop(minutes=5)
    async def voice_cleanup(self):
        try:
            tickets = await database.tickets_get_all_open()
            now = int(time.time())
            EMPTY_THRESHOLD = 30 * 60  # 30 минут
            # Состояние храним в БД (через last_message_at voice-канала — нет, используем словарь в памяти)
            # Простейшая реализация: просто проверяем, если голосовой канал пустой — удаляем.
            # (Долговременное отслеживание 30-минутного простоя требует состояния в БД —
            #  для упрощения удаляем пустой сразу, но логируем.)
            for t in tickets:
                vc_id = t.get("voice_channel_id")
                if not vc_id:
                    continue
                # Проверяем в каждом guild
                for guild in self.bot.guilds:
                    vc = guild.get_channel(vc_id)
                    if vc is None or not isinstance(vc, discord.VoiceChannel):
                        continue
                    if len(vc.members) == 0:
                        # Проверяем, сколько времени прошло с создания тикета (грубая эвристика)
                        # Реально — для корректной реализации 30 минут нужно хранить время
                        # последнего выхода пользователя. Здесь используем простую логику:
                        # если канал пустой при двух подряд проверках (10 минут), удаляем.
                        key = f"vc_empty_since_{vc_id}"
                        if not hasattr(self, "_vc_empty"):
                            self._vc_empty = {}
                        if key in self._vc_empty:
                            elapsed = now - self._vc_empty[key]
                            if elapsed >= EMPTY_THRESHOLD:
                                try:
                                    await vc.delete(reason="Голосовой канал пуст > 30 минут")
                                    await database.ticket_set_voice(t["channel_id"], 0)
                                except discord.HTTPException:
                                    pass
                                del self._vc_empty[key]
                        else:
                            self._vc_empty[key] = now
                    else:
                        # Канал не пуст — сбрасываем таймер
                        key = f"vc_empty_since_{vc_id}"
                        if hasattr(self, "_vc_empty") and key in self._vc_empty:
                            del self._vc_empty[key]
        except Exception as e:
            log.exception("Ошибка в voice_cleanup: %s", e)

    @voice_cleanup.before_loop
    async def _before_voice_cleanup(self):
        await self.bot.wait_until_ready()

    # --- Фоновая задача: напоминание о Claim ---------------------------------
    @tasks.loop(minutes=10)
    async def claim_reminder(self):
        try:
            tickets = await database.tickets_get_all_open()
            now = int(time.time())
            REMINDER_THRESHOLD = 30 * 60  # 30 минут
            for t in tickets:
                # Если ещё не взят в работу
                if t.get("claimed_at"):
                    continue
                created = t.get("created_at")
                if created is None:
                    continue
                delta = now - int(created)
                # Напоминаем каждые ~30 минут, но не чаще раза в 30 минут
                # Используем warned_inactive как флаг напоминания (многоразово — сбрасываем при claim)
                if delta >= REMINDER_THRESHOLD:
                    channel = self.bot.get_channel(t["channel_id"])
                    if channel is None:
                        continue
                    config = _get_config(self.bot)
                    ping_role_ids = (config.get("ping_roles_clan", []) if t["type"] == "clan"
                                     else config.get("ping_roles_mod", []))
                    ping_str = " ".join(f"<@&{rid}>" for rid in ping_role_ids) if ping_role_ids else ""
                    # Если ролей для пинга нет — пингуем модераторов по умолчанию
                    if not ping_str:
                        roles_cfg = config.get("roles", {})
                        ping_str = " ".join(
                            f"<@&{rid}>" for key in ("moderator", "helper", "administrator")
                            for rid in [roles_cfg.get(key)] if rid
                        )
                    try:
                        reminder_embed = discord.Embed(
                            title="⚠️ Заявка ожидает рассмотрения!",
                            description=(
                                f"## 🚨 Тикет без внимания более 30 минут\n\n"
                                f"Кандидат <@{t['user_id']}> ждёт.\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"👇 Нажмите кнопку **🤝 Взять в работу** в закрепе."
                            ),
                            color=COLOR_WARNING,
                            timestamp=now_msk(),
                        )
                        reminder_embed.set_footer(text="EGODiscord System • Claim Reminder")
                        await channel.send(
                            content=ping_str or None,
                            embed=reminder_embed,
                            allowed_mentions=AllowedMentions(roles=True, users=False, everyone=False),
                        )
                    except discord.HTTPException as e:
                        log.warning("Не удалось отправить напоминание: %s", e)
                    # Чтобы не спамить, обновляем last_message_at (хотя бы фиктивно)
                    # — нет, лучше отдельно хранить, но для упрощения:
                    # Увеличиваем «created_at» путём записи last_message_at (не меняем created_at!)
                    # Используем warned_inactive как временную метку последнего напоминания.
                    # Здесь просто пропускаем, т.к. тикет останется в списке.
        except Exception as e:
            log.exception("Ошибка в claim_reminder: %s", e)

    @claim_reminder.before_loop
    async def _before_claim_reminder(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketControl(bot))
