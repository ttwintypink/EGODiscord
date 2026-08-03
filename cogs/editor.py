"""
cogs/editor.py — Удобный редактор бота для администрации EGO.

Команды:
    .editor              — открыть интерактивный дашборд настройки бота
                           (кнопки: вопросы клана, вопросы модеров, текст панели,
                            пинг-роли, роли персонала, ключ Steam, каналы/категории)
    .editor questions    — то же самое, что и кнопка «Вопросы» (без дашборда)
    .editor panel-text   — то же самое, что и кнопка «Текст панели»

Дашборд использует кнопки с цветовой кодировкой и live-предпросмотром текущих значений.
Все изменения сразу сохраняются в config.json И в атрибуте bot._config,
поэтому применяются мгновенно (без перезапуска бота).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

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
        # Пишем во временный файл, потом переименовываем — атомарность.
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


# ============================================================================
# Модалки редактирования
# ============================================================================

class EditQuestionsModal(ui.Modal):
    """Редактирование списка вопросов анкеты (до 5)."""

    def __init__(self, config: dict, ticket_type: str, parent_view: "EditorDashboardView"):
        title = "🛡️ Вопросы анкеты — Клан" if ticket_type == "clan" \
                else "👑 Вопросы анкеты — Модерация"
        super().__init__(title=title[:45])
        self.config = config
        self.ticket_type = ticket_type
        self.parent_view = parent_view

        key = "questions_clan" if ticket_type == "clan" else "questions_mod"
        current = config.get(key, [])
        self._inputs: list[ui.TextInput] = []
        for i in range(5):
            cur = current[i] if i < len(current) else ""
            inp = ui.TextInput(
                label=f"Вопрос {i + 1}",
                placeholder="Пусто = удалить этот вопрос",
                default=cur,
                required=False,
                max_length=200,
                style=discord.TextStyle.short,
            )
            self._inputs.append(inp)
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        new_q = [inp.value.strip() for inp in self._inputs
                 if inp.value and inp.value.strip()]
        key = "questions_clan" if self.ticket_type == "clan" else "questions_mod"
        self.config[key] = new_q
        if not _save_config(self.config):
            await interaction.response.send_message(
                embed=build_error(description="Не удалось сохранить config.json"),
                ephemeral=True,
            )
            return

        type_label = "Клан 🛡️" if self.ticket_type == "clan" else "Модерация 👑"
        preview = "\n".join(f"**{i + 1}.** {q}" for i, q in enumerate(new_q)) or "—"
        await interaction.response.send_message(
            embed=build_success(
                title=f"✅ Вопросы обновлены — {type_label}",
                description=f"Количество вопросов: **{len(new_q)}**",
                fields=[("Новые вопросы", preview, False)],
                footer_text="EGODiscord System • Editor",
            ),
            ephemeral=True,
        )
        # Обновляем дашборд
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditPanelTextModal(ui.Modal):
    """Редактирование текста панели тикетов."""

    def __init__(self, config: dict, parent_view: "EditorDashboardView"):
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
                footer_text="EGODiscord System • Editor",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditSteamKeyModal(ui.Modal):
    """Редактирование Steam API ключа."""

    def __init__(self, config: dict, parent_view: "EditorDashboardView"):
        super().__init__(title="🔑 Steam API ключ")
        self.config = config
        self.parent_view = parent_view
        current = config.get("steam_api_key", "")
        masked = current[:6] + "…" + current[-4:] if len(current) > 10 else "—"
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
                description=f"Новый ключ: `{masked}`\n\n"
                            f"Проверка аккаунтов теперь будет идти через Steam Web API "
                            f"(VAC-баны, часы в Rust, и т.п.).",
                footer_text="EGODiscord System • Editor",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditChannelIdsModal(ui.Modal):
    """Редактирование ID каналов/категорий."""

    def __init__(self, config: dict, parent_view: "EditorDashboardView"):
        super().__init__(title="📁 Каналы и категории")
        self.config = config
        self.parent_view = parent_view

        self.cat_clan = ui.TextInput(
            label="ID категории — Клан",
            placeholder="1533566111684235504",
            default=str(config.get("category_clan_id", "")),
            required=True,
            max_length=20,
            style=discord.TextStyle.short,
        )
        self.cat_mod = ui.TextInput(
            label="ID категории — Модерация",
            placeholder="1533566150015848588",
            default=str(config.get("category_mod_id", "")),
            required=True,
            max_length=20,
            style=discord.TextStyle.short,
        )
        self.log_ch = ui.TextInput(
            label="ID канала логов",
            placeholder="1533068805771628637",
            default=str(config.get("log_channel_id", "")),
            required=True,
            max_length=20,
            style=discord.TextStyle.short,
        )
        self.accept_role = ui.TextInput(
            label="ID роли при принятии (EGO)",
            placeholder="1533070154349674526",
            default=str(config.get("accept_role_id", "")),
            required=True,
            max_length=20,
            style=discord.TextStyle.short,
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
                embed=build_error(
                    description="Все ID должны быть числами. Скопируйте ID через "
                                "Правый клик по каналу/роли → Копировать ID "
                                "(нужна включённая «Разработка» в Discord)."
                ),
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
                footer_text="EGODiscord System • Editor",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditRolesModal(ui.Modal):
    """Редактирование ролей персонала (leader, co_leader, ...)."""

    def __init__(self, config: dict, parent_view: "EditorDashboardView"):
        super().__init__(title="👑 Роли персонала EGO")
        self.config = config
        self.parent_view = parent_view
        roles_cfg = config.get("roles", {})

        self.inputs: dict[str, ui.TextInput] = {}
        # Только 5 самых важных (модалка вмещает 5)
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
                required=False,
                max_length=20,
                style=discord.TextStyle.short,
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
                footer_text="EGODiscord System • Editor",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


class EditPingRolesModal(ui.Modal):
    """Редактирование ролей для пинга (clan/mod)."""

    def __init__(self, config: dict, parent_view: "EditorDashboardView"):
        super().__init__(title="🔔 Роли для пинга при создании тикета")
        self.config = config
        self.parent_view = parent_view

        clan_ping = config.get("ping_roles_clan", [])
        mod_ping = config.get("ping_roles_mod", [])

        self.clan_input = ui.TextInput(
            label="Пинг при тикете Клан (через запятую)",
            placeholder="1533067469894189167, 1533560996772188211",
            default=", ".join(str(r) for r in clan_ping) if clan_ping else "",
            required=False,
            max_length=200,
            style=discord.TextStyle.short,
        )
        self.mod_input = ui.TextInput(
            label="Пинг при тикете Модерация (через запятую)",
            placeholder="1533067469894189167",
            default=", ".join(str(r) for r in mod_ping) if mod_ping else "",
            required=False,
            max_length=200,
            style=discord.TextStyle.short,
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
                footer_text="EGODiscord System • Editor",
            ),
            ephemeral=True,
        )
        try:
            await self.parent_view.message.edit(embed=_dashboard_embed(self.config))
        except (discord.HTTPException, AttributeError):
            pass


# ============================================================================
# Дашборд (главное меню редактора)
# ============================================================================

def _dashboard_embed(config: dict) -> discord.Embed:
    """Собирает embed-превью текущих настроек бота."""
    questions_clan = config.get("questions_clan", [])
    questions_mod = config.get("questions_mod", [])
    steam_key = config.get("steam_api_key", "")
    masked_key = (steam_key[:6] + "…" + steam_key[-4:]) if len(steam_key) > 10 else "—"
    roles_cfg = config.get("roles", {})

    embed = discord.Embed(
        title="🛠️ Редактор бота EGO",
        description=(
            f"## ⚙️ Текущие настройки\n\n"
            f"Все изменения применяются **мгновенно** — без перезапуска бота.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ **Вопросы анкеты (Клан):** {len(questions_clan)} шт.\n"
            f"👑 **Вопросы анкеты (Модерация):** {len(questions_mod)} шт.\n"
            f"📝 **Текст панели:** {_truncate(config.get('ticket_panel_text', ''), 60)}\n"
            f"🔑 **Steam API ключ:** `{masked_key}`\n"
            f"🔔 **Пинг ролей (Клан/Модер):** "
            f"{len(config.get('ping_roles_clan', []))} / "
            f"{len(config.get('ping_roles_mod', []))}\n"
            f"👑 **Ролей персонала:** {len([k for k, v in roles_cfg.items() if v])} / 7\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 Нажмите кнопку ниже, чтобы открыть нужный раздел"
        ),
        color=COLOR_MAIN,
        timestamp=embeds.now_msk(),
    )
    embed.set_footer(text="EGODiscord System • Editor Dashboard")
    return embed


class EditorDashboardView(ui.View):
    """Главная панель редактора с кнопками по разделам."""

    def __init__(self, config: dict, owner_id: int):
        super().__init__(timeout=300)  # 5 минут — потом кнопки отключаются
        self.config = config
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Только тот, кто открыл дашборд, может им управлять
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
        """Через 5 минут отключаем все кнопки."""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    # --- Кнопки разделов ---

    @ui.button(label="Вопросы — Клан", emoji="🛡️",
               style=discord.ButtonStyle.primary, custom_id="ego_edit_q_clan")
    async def btn_q_clan(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditQuestionsModal(self.config, "clan", self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Вопросы — Модерация", emoji="👑",
               style=discord.ButtonStyle.primary, custom_id="ego_edit_q_mod")
    async def btn_q_mod(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditQuestionsModal(self.config, "mod", self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Текст панели", emoji="📝",
               style=discord.ButtonStyle.secondary, custom_id="ego_edit_panel")
    async def btn_panel(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditPanelTextModal(self.config, self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Steam API ключ", emoji="🔑",
               style=discord.ButtonStyle.secondary, custom_id="ego_edit_steam")
    async def btn_steam(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditSteamKeyModal(self.config, self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Пинг-роли", emoji="🔔",
               style=discord.ButtonStyle.secondary, custom_id="ego_edit_ping")
    async def btn_ping(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditPingRolesModal(self.config, self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Роли персонала", emoji="👑",
               style=discord.ButtonStyle.secondary, custom_id="ego_edit_roles")
    async def btn_roles(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditRolesModal(self.config, self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Каналы и категории", emoji="📁",
               style=discord.ButtonStyle.secondary, custom_id="ego_edit_channels")
    async def btn_channels(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditChannelIdsModal(self.config, self)
        await interaction.response.send_modal(modal)

    @ui.button(label="🔁 Пересоздать панель", emoji="♻️",
               style=discord.ButtonStyle.success, custom_id="ego_edit_recreate_panel")
    async def btn_recreate(self, interaction: discord.Interaction, button: ui.Button):
        """Пересоздаёт панель тикетов в текущем канале (со старым сообщением — удаляет)."""
        await interaction.response.defer(ephemeral=True)
        # Найти и удалить старое сообщение панели в этом канале
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

        # Создаём новую панель
        from cogs.tickets import TicketPanelView, _build_panel_embed  # type: ignore
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

    @ui.button(label="Закрыть редактор", emoji="✖️",
               style=discord.ButtonStyle.danger, custom_id="ego_edit_close")
    async def btn_close(self, interaction: discord.Interaction, button: ui.Button):
        # Убираем view (кнопки)
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass


# ============================================================================
# Cog
# ============================================================================

class Editor(commands.Cog):
    """Интерактивный редактор настроек бота."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    @commands.command(name="editor")
    @commands.guild_only()
    async def editor_cmd(self, ctx: commands.Context):
        """
        Открыть интерактивный дашборд настройки бота.

        Дашборд показывает все текущие значения и позволяет изменить:
        - Вопросы анкеты (клан/модерация)
        - Текст панели тикетов
        - Steam API ключ
        - Пинг-роли
        - Роли персонала
        - Каналы и категории
        - Пересоздать панель
        """
        config = self._config()
        if not _is_admin(ctx.author, config):
            await ctx.send(embed=embeds.error_no_permission())
            return

        view = EditorDashboardView(config, ctx.author.id)
        msg = await ctx.send(embed=_dashboard_embed(config), view=view)
        view.message = msg

        # Удаляем сообщение с командой
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Editor(bot))
