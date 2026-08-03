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

        embed = build_success(
            title="✅ Вопросы обновлены",
            description=f"Тип: **{'Клан' if self.ticket_type == 'clan' else 'Модерация'}**\n"
                        f"Количество вопросов: **{len(new_questions)}**",
            fields=[("Вопросы", "\n".join(f"{i+1}. {q}" for i, q in enumerate(new_questions)) or "—", False)],
        )
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
        embed = build_info(
            title="🚫 Blacklist — управление",
            description="Команды для управления чёрным списком пользователей.",
            fields=[
                ("`.blacklist add <ID>`", "Добавить пользователя в чёрный список", False),
                ("`.blacklist remove <ID>`", "Удалить пользователя из чёрного списка", False),
                ("`.blacklist list`", "Показать список заблокированных", False),
            ],
        )
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
                description="Статистика пока пуста. Закройте первый тикет, чтобы данные появились.",
            ))
            return

        embed = build_main(
            title="📊 ТОП рекрутеров EGO",
            description="Статистика накапливается бесконечно (без сброса).",
        )

        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        for i, row in enumerate(top):
            recruiter_id = row["recruiter_id"]
            ticket_count = row["ticket_count"]
            total_stars = row["total_stars"]
            ratings_count = row["ratings_count"]
            total_reaction_time = row["total_reaction_time"]

            avg_rating = (total_stars / ratings_count) if ratings_count else 0
            avg_reaction = (total_reaction_time / ticket_count) if ticket_count else 0

            # Читаемое время реакции
            if avg_reaction >= 3600:
                reaction_str = f"{avg_reaction/3600:.1f} ч"
            elif avg_reaction >= 60:
                reaction_str = f"{avg_reaction/60:.1f} мин"
            else:
                reaction_str = f"{avg_reaction:.0f} сек"

            stars_str = "⭐" * round(avg_rating) if avg_rating else "—"
            embed.add_field(
                name=f"{medals[i]} <@{recruiter_id}> (`{recruiter_id}`)",
                value=(
                    f"Тикетов закрыто: **{ticket_count}**\n"
                    f"Средняя оценка: **{avg_rating:.1f}/5** {stars_str} "
                    f"({ratings_count} оценок)\n"
                    f"Средняя скорость реакции: **{reaction_str}**"
                ),
                inline=False,
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
        """Открывает форму для админов, чтобы изменить вопросы для анкеты."""
        if not _is_admin(ctx.author, self._config()):
            await ctx.send(embed=embeds.error_no_permission())
            return

        view = ChooseQuestionsTypeView(self._config())
        embed = build_main(
            title="⚙️ Изменение вопросов анкеты",
            description="Выберите, для какого типа заявок изменить вопросы.",
        )
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
