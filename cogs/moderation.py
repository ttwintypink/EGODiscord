"""
cogs/moderation.py — Команды администрации:
    .blacklist add <ID> [@user]   — добавить в чёрный список
    .blacklist remove <ID>        — удалить из чёрного списка
    .blacklist list               — показать список
    .stats                        — ТОП рекрутеров со средней оценкой
                                    и скоростью реакции
    .setup-voprosy                — изменить вопросы анкеты (форма)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import discord
from discord import ui
from discord.ext import commands

import database
from utils import embeds
from utils.embeds import (
    build_main, build_success, build_error, build_warning, build_info,
    msk_timestamp,
)

log = logging.getLogger(__name__)


def _is_staff(member: discord.Member, config: dict) -> bool:
    role_ids = set(r.id for r in member.roles)
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator", "moderator", "helper"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return member.guild_permissions.administrator


def _is_admin(member: discord.Member, config: dict) -> bool:
    role_ids = set(r.id for r in member.roles)
    roles_cfg = config.get("roles", {})
    for key in ("leader", "co_leader", "administrator"):
        rid = roles_cfg.get(key)
        if rid and rid in role_ids:
            return True
    return member.guild_permissions.administrator


# ============================================================================
# Форма изменения вопросов анкеты
# ============================================================================

class EditQuestionsModal(ui.Modal, title="⚙️ Изменение вопросов анкеты"):
    def __init__(self, config: dict, ticket_type: str):
        super().__init__()
        self.config = config
        self.ticket_type = ticket_type
        key = "questions_clan" if ticket_type == "clan" else "questions_mod"
        current = config.get(key, [])
        self._inputs = []
        # 5 полей — каждое соответствует одному вопросу
        for i in range(5):
            current_val = current[i] if i < len(current) else ""
            inp = ui.TextInput(
                label=f"Вопрос {i + 1}",
                placeholder="Оставьте пустым, чтобы удалить вопрос",
                default=current_val,
                required=False,
                max_length=200,
                style=discord.TextStyle.short,
            )
            self._inputs.append(inp)
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        new_questions = [inp.value.strip() for inp in self._inputs if inp.value and inp.value.strip()]
        key = "questions_clan" if self.ticket_type == "clan" else "questions_mod"
        self.config[key] = new_questions

        # Сохраняем в config.json
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg[key] = new_questions
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.exception("Не удалось сохранить config.json: %s", e)
            await interaction.response.send_message(
                embed=build_error(description=f"Не удалось сохранить конфиг: `{e}`"),
                ephemeral=True,
            )
            return

        type_label = "🛡️ Клан" if self.ticket_type == "clan" else "👑 Модерация"
        preview = "\n".join(f"**{i+1}.** {q}" for i, q in enumerate(new_questions)) or "—"
        embed = discord.Embed(
            title="✅ Вопросы обновлены",
            description=(
                f"## 📝 {type_label}\n\n"
                f"Количество вопросов: **{len(new_questions)}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0x57F287,
            timestamp=embeds.now_msk(),
        )
        embed.add_field(name="Новые вопросы", value=preview, inline=False)
        embed.set_footer(text="EGODiscord System • Editor")
        try:
            await interaction.response.send_message(embed=embed)
        except discord.HTTPException:
            pass


# ============================================================================
# Cog
# ============================================================================

class Moderation(commands.Cog):
    """Команды администрации."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    # --- .blacklist ----------------------------------------------------------
    @commands.group(name="blacklist", invoke_without_command=True)
    @commands.guild_only()
    async def blacklist_grp(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🚫 Blacklist — управление",
            description=(
                "## 🛡️ Управление чёрным списком\n\n"
                "Команды для управления чёрным списком пользователей.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0x5865F2,
            timestamp=embeds.now_msk(),
        )
        embed.add_field(
            name="➕ Добавить",
            value="`.blacklist add <ID>`\nДобавить пользователя в ЧС",
            inline=False,
        )
        embed.add_field(
            name="➖ Удалить",
            value="`.blacklist remove <ID>`\nУдалить пользователя из ЧС",
            inline=False,
        )
        embed.add_field(
            name="📋 Список",
            value="`.blacklist list`\nПоказать всех заблокированных",
            inline=False,
        )
        embed.set_footer(text="EGODiscord System • Blacklist")
        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            pass

    @blacklist_grp.command(name="add")
    async def blacklist_add(self, ctx: commands.Context, user: discord.User | int):
        if not _is_admin(ctx.author, self._config()):
            await ctx.send(embed=embeds.error_no_permission())
            return

        if isinstance(user, discord.User):
            user_id = user.id
        else:
            user_id = int(user)

        reason = ""
        # Если команда вызвана с реплаем — берём причину из контекста
        # (упрощённо — причина = "Добавлено модератором {author}")
        reason = f"Добавлено: {ctx.author}"

        try:
            await database.blacklist_add(user_id, ctx.author.id, reason)
        except Exception as e:
            await ctx.send(embed=build_error(description=f"Ошибка БД: `{e}`"))
            return

        embed = build_success(
            title="🚫 Пользователь добавлен в чёрный список",
            description=f"ID: `{user_id}`\nДобавил: {ctx.author.mention}\nВремя: {msk_timestamp()}",
        )
        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            pass

    @blacklist_grp.command(name="remove")
    async def blacklist_remove(self, ctx: commands.Context, user_id: int):
        if not _is_admin(ctx.author, self._config()):
            await ctx.send(embed=embeds.error_no_permission())
            return
        try:
            removed = await database.blacklist_remove(user_id)
        except Exception as e:
            await ctx.send(embed=build_error(description=f"Ошибка БД: `{e}`"))
            return

        if removed:
            embed = build_success(
                title="✅ Пользователь удалён из чёрного списка",
                description=f"ID: `{user_id}`\nУдалил: {ctx.author.mention}",
            )
        else:
            embed = build_warning(
                title="⚠️ Не найдено",
                description=f"Пользователь с ID `{user_id}` не найден в чёрном списке.",
            )
        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            pass

    @blacklist_grp.command(name="list")
    async def blacklist_list(self, ctx: commands.Context):
        if not _is_staff(ctx.author, self._config()):
            await ctx.send(embed=embeds.error_no_permission())
            return
        try:
            rows = await database.blacklist_list()
        except Exception as e:
            await ctx.send(embed=build_error(description=f"Ошибка БД: `{e}`"))
            return

        if not rows:
            embed = build_info(
                title="🚫 Чёрный список",
                description="Список пуст.",
            )
            await ctx.send(embed=embed)
            return

        # Пагинация простая — первые 25 (как лимит полей Embed)
        embed = build_main(
            title="🚫 Чёрный список",
            description=f"Всего заблокировано: **{len(rows)}**",
        )
        for user_id, added_by, added_at, reason in rows[:25]:
            from datetime import datetime, timezone, timedelta
            MSK = timezone(timedelta(hours=3))
            ts = datetime.fromtimestamp(added_at, MSK).strftime("%d.%m.%Y %H:%M")
            embed.add_field(
                name=f"ID: {user_id}",
                value=f"Добавил: <@{added_by}> (`{added_by}`)\nВремя: {ts}\nПричина: {reason or '—'}",
                inline=False,
            )

        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            pass

    # --- .stats --------------------------------------------------------------
    @commands.command(name="stats")
    @commands.guild_only()
    async def stats_cmd(self, ctx: commands.Context):
        """Показывает ТОП рекрутеров со средней оценкой и скоростью реакции."""
        if not _is_staff(ctx.author, self._config()):
            await ctx.send(embed=embeds.error_no_permission())
            return

        try:
            top = await database.stats_top(limit=10)
        except Exception as e:
            await ctx.send(embed=build_error(description=f"Ошибка БД: `{e}`"))
            return

        if not top:
            await ctx.send(embed=build_info(
                title="📊 Статистика рекрутеров",
                description=(
                    "## 📈 Статистика пуста\n\n"
                    "Пока нет закрытых тикетов.\n"
                    "Закройте первый тикет, чтобы данные появились."
                ),
            ))
            return

        # Считаем общую статистику
        total_tickets = sum(r["ticket_count"] for r in top)
        total_ratings = sum(r["ratings_count"] for r in top)
        all_stars = sum(r["total_stars"] for r in top)
        avg_all = (all_stars / total_ratings) if total_ratings else 0

        # Текстовое описание лидерборда
        lines = []
        medals = ["🥇", "🥈", "🥉"] + [f"**{i+1}.**" for i in range(3, 10)]
        for i, row in enumerate(top[:10]):
            recruiter_id = row["recruiter_id"]
            ticket_count = row["ticket_count"]
            total_stars = row["total_stars"]
            ratings_count = row["ratings_count"]
            total_reaction_time = row["total_reaction_time"]

            avg_rating = (total_stars / ratings_count) if ratings_count else 0
            avg_reaction = (total_reaction_time / ticket_count) if ticket_count else 0

            # Читаемое время реакции
            if avg_reaction >= 3600:
                reaction_str = f"{avg_reaction/3600:.1f}ч"
            elif avg_reaction >= 60:
                reaction_str = f"{avg_reaction/60:.1f}мин"
            else:
                reaction_str = f"{avg_reaction:.0f}сек"

            stars_str = "⭐" * round(avg_rating) if avg_rating else "—"
            medal = medals[i] if i < len(medals) else f"**{i+1}.**"

            lines.append(
                f"{medal} <@{recruiter_id}> `{recruiter_id}`\n"
                f"   ┣ 📋 Тикетов: **{ticket_count}**\n"
                f"   ┣ ⭐ Оценка: **{avg_rating:.1f}/5** {stars_str} ({ratings_count} оценок)\n"
                f"   ┗ ⚡ Реакция: **{reaction_str}**"
            )

        embed = discord.Embed(
            title="🏆 Лидерборд рекрутеров EGO",
            description=(
                f"## 📊 ТОП-{min(10, len(top))} рекрутеров\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 **Всего тикетов закрыто:** {total_tickets}\n"
                f"⭐ **Средняя оценка клана:** {avg_all:.1f}/5\n"
                f"👥 **Активных рекрутеров:** {len(top)}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                + "\n\n".join(lines)
            ),
            color=0x5865F2,
            timestamp=embeds.now_msk(),
        )
        embed.set_footer(text=f"EGODiscord System • {msk_timestamp()}")

        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            pass

    # --- .setup-voprosy ------------------------------------------------------
    @commands.command(name="setup-voprosy")
    @commands.guild_only()
    async def setup_voprosy(self, ctx: commands.Context):
        """
        Открывает форму для админов, чтобы изменить вопросы для анкеты.
        Совет: используйте `.editor` для полноценного дашборда настройки.
        """
        if not _is_admin(ctx.author, self._config()):
            await ctx.send(embed=embeds.error_no_permission())
            return

        view = ChooseQuestionsTypeView(self._config())
        embed = discord.Embed(
            title="⚙️ Изменение вопросов анкеты",
            description=(
                "## 📝 Выберите тип заявок\n\n"
                "Выберите, для какого типа заявок изменить вопросы.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💡 **Совет:** используйте `.editor` для полноценного дашборда "
                f"с всеми настройками бота."
            ),
            color=0x5865F2,
            timestamp=embeds.now_msk(),
        )
        embed.set_footer(text="EGODiscord System • Question Editor")
        try:
            await ctx.send(embed=embed, view=view)
        except discord.HTTPException:
            pass


class ChooseQuestionsTypeView(ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=120)
        self.config = config

    @ui.button(label="Клан 🛡️", style=discord.ButtonStyle.primary, custom_id="ego_setup_q_clan")
    async def clan_btn(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditQuestionsModal(self.config, "clan")
        await interaction.response.send_modal(modal)

    @ui.button(label="Модерация 👑", style=discord.ButtonStyle.primary, custom_id="ego_setup_q_mod")
    async def mod_btn(self, interaction: discord.Interaction, button: ui.Button):
        modal = EditQuestionsModal(self.config, "mod")
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
