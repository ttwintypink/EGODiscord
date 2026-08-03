"""
cogs/rules.py — Правила клана EGO.

Команды:
    .rules              — показать правила в текущем канале
    .setuprules         — установить правила как закреплённое сообщение
                          (только для лидерства/разработчика)

Правила хранятся в config.json → rules_sections (можно редактировать через
.editor в будущем). По умолчанию — стандартный набор клана EGO.

Дизайн:
    • Премиум-embed с разделами по категориям
    • Единый стиль с остальными сообщениями бота
    • Московское время в timestamp
    • Эмодзи-иконки для каждой категории
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import ui
from discord.ext import commands

from utils import embeds
from utils.embeds import (
    build_main, build_success, build_error, build_info,
    msk_timestamp, now_msk,
    COLOR_MAIN, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
)

log = logging.getLogger(__name__)


# ============================================================================
# Стандартные правила клана EGO (если в config.json нет своих)
# ============================================================================

DEFAULT_RULES_TITLE = "📜 ПРАВИЛА КЛАНА EGO"

DEFAULT_RULES_INTRO = (
    "## ⚜️ Добро пожаловать в клан **EGO**\n\n"
    "Соблюдение этих правил — основа нашей силы и сплочённости. "
    "Незнание правил не освобождает от ответственности.\n\n"
    "Прочитайте каждый раздел внимательно. "
    "По вопросам — обращайтесь к лидерству клана.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)

DEFAULT_RULES_SECTIONS = [
    {
        "emoji": "👑",
        "title": "1. Иерархия и руководство",
        "rules": [
            "1.1. Фамус всегда прав.",
            "1.2. Если Фамус не прав, то см. пункт 1.1.",
            "1.3. Вы должны слушаться лидера, зам. лидера в любом случае.",
            "1.4. Решения лидерства обсуждению не подлежат — есть вопросы, задавайте в личку.",
            "1.5. Уважайте старших по званию: лидер → зам.лидера → админ → модератор → хелпер.",
            "1.6. Спор с лидерством при свидетелях = предупреждение, повторно — кик.",
        ],
    },
    {
        "emoji": "🤝",
        "title": "2. Поведение и общение",
        "rules": [
            "2.1. Запрещены оскорбления по национальному, расовому или религиозному признаку.",
            "2.2. Запрещён мат в адрес соклановцев и администрации.",
            "2.3. Запрещён спам, флуд, капс в текстовых и голосовых каналах.",
            "2.4. Конфликты внутри клана решайте через администрацию, а не публично.",
            "2.5. Уважайте друг друга — мы одна команда.",
            "2.6. Реклама других кланов/проектов — мгновенный бан.",
        ],
    },
    {
        "emoji": "🎮",
        "title": "3. Активность и участие",
        "rules": [
            "3.1. Минимальная активность — 3 дня в неделю. Дольше — кик без предупреждения.",
            "3.2. Отсутствие более 7 дней — отпишитесь в канале #отсутствие с причиной.",
            "3.3. Участие в клановых войнах и ивентах обязательно (если онлайн).",
            "3.4. Заходите в Discord хотя бы раз в день — читать анонсы.",
            "3.5. Долгое отсутствие без предупреждения = автоматический кик из клана.",
        ],
    },
    {
        "emoji": "⚔️",
        "title": "4. Игровая этика (Rust)",
        "rules": [
            "4.1. Запрещён чит, багоюз, использование стороннего ПО — мгновенный бан.",
            "4.2. Запрещено нападать на союзников и дружественные кланы.",
            "4.3. Лут соклановцев не трогать без разрешения.",
            "4.4. Помогайте новичкам клана — вы тоже были ими.",
            "4.5. В raid-времена все ресурсы клана идут на общее дело.",
            "4.6. Соло-рейды без согласования с лидерством — запрещены.",
        ],
    },
    {
        "emoji": "🚨",
        "title": "5. Дисциплина и санкции",
        "rules": [
            "5.1. Первое нарушение — устное предупреждение.",
            "5.2. Второе — мут на 24 часа в Discord.",
            "5.3. Третье — кик из клана.",
            "5.4. Грубые нарушения (чит, оскорбление лидерства, предательство) — бан без предупреждения.",
            "5.5. Решение о наказании принимает лидер или зам.лидера. Обжалование — в личку.",
            "5.6. Амнистия возможна через 30 дней после наказания — по решению лидера.",
        ],
    },
]

DEFAULT_RULES_OUTRO_TEMPLATE = (
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "## ⚜️ Сила клана — в дисциплине\n\n"
    "Соблюдая эти правила, вы делаете **EGO** сильнее. "
    "Мы растим сообщество, где каждый может стать легендой Rust.\n\n"
    "*Правила обновлены: {timestamp}*"
)


# ============================================================================
# Загрузка правил из конфига
# ============================================================================

def _load_rules(config: dict) -> tuple[str, str, list[dict], str]:
    """Загружает правила из config.json или возвращает дефолтные.

    Возвращает кортеж (title, intro, sections, outro).
    """
    rules_cfg = config.get("rules", {})
    if not isinstance(rules_cfg, dict):
        rules_cfg = {}

    title = rules_cfg.get("title") or DEFAULT_RULES_TITLE
    intro = rules_cfg.get("intro") or DEFAULT_RULES_INTRO
    # outro вычисляем динамически (с актуальным МСК-временем)
    outro_raw = rules_cfg.get("outro") or DEFAULT_RULES_OUTRO_TEMPLATE
    try:
        outro = outro_raw.format(timestamp=msk_timestamp())
    except (KeyError, IndexError):
        outro = outro_raw

    sections_raw = rules_cfg.get("sections")
    if isinstance(sections_raw, list) and sections_raw:
        sections = []
        for s in sections_raw:
            if not isinstance(s, dict):
                continue
            emoji = str(s.get("emoji", "📋"))[:8]
            s_title = str(s.get("title", "Раздел"))[:80]
            s_rules = s.get("rules", [])
            if not isinstance(s_rules, list):
                continue
            s_rules = [str(r)[:300] for r in s_rules if r][:25]
            if s_rules:
                sections.append({
                    "emoji": emoji,
                    "title": s_title,
                    "rules": s_rules,
                })
        if sections:
            return title, intro, sections, outro

    return title, intro, DEFAULT_RULES_SECTIONS, outro


# ============================================================================
# Сборка embed-а правил
# ============================================================================

def build_rules_embed(config: dict) -> discord.Embed:
    """Собирает премиум-embed с правилами клана."""
    title, intro, sections, outro = _load_rules(config)

    embed = discord.Embed(
        title=title,
        description=intro,
        color=COLOR_MAIN,
        timestamp=now_msk(),
    )

    # Каждый раздел — отдельное поле
    for section in sections:
        emoji = section["emoji"]
        s_title = section["title"]
        # Каждое правило с новой строки, в код-блок для красоты
        rules_text = "\n".join(f"▸ {r}" for r in section["rules"])
        embed.add_field(
            name=f"{emoji} {s_title}",
            value=rules_text,
            inline=False,
        )

    # Финальный блок — outro (если помещается в description, добавляем к нему;
    # но так как description уже занят intro, делаем outro отдельным полем)
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        value=outro,
        inline=False,
    )

    embed.set_footer(text="EGODiscord System • Правила клана EGO")
    return embed


# ============================================================================
# Cog
# ============================================================================

class RulesCog(commands.Cog):
    """Правила клана EGO."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------------
    # .rules — показать правила в текущем канале
    # ------------------------------------------------------------------------

    @commands.command(name="rules", aliases=["правила", "ruleset"])
    @commands.guild_only()
    async def cmd_rules(self, ctx: commands.Context):
        """📜 Показать правила клана EGO.

        Пример:
            .rules
        """
        config = getattr(self.bot, "config", {})
        embed = build_rules_embed(config)
        try:
            await ctx.send(embed=embed)
        except discord.HTTPException as e:
            log.warning("Не удалось отправить правила: %s", e)
            await ctx.send("❌ Не удалось отправить правила. Сообщите разработчику.")

    # ------------------------------------------------------------------------
    # .setuprules — установить правила как закреплённое сообщение
    # ------------------------------------------------------------------------

    @commands.command(name="setuprules", aliases=["installrules", "rulespanel"])
    @commands.guild_only()
    async def cmd_setup_rules(self, ctx: commands.Context):
        """📜 Установить правила в этом канале (для лидерства).

        Бот отправит embed с правилами и попытается его закрепить.
        Используйте в выделенном канале #правила.

        Пример:
            .setuprules
        """
        config = getattr(self.bot, "config", {})
        member = ctx.author

        # Проверка прав: только лидерство или разработчик
        dev_id = config.get("developer_id", 0)
        roles_cfg = config.get("roles", {})
        allowed_role_ids = {
            roles_cfg.get("leader"),
            roles_cfg.get("co_leader"),
            roles_cfg.get("administrator"),
        }
        user_role_ids = {r.id for r in member.roles} if hasattr(member, "roles") else set()

        if member.id != dev_id and not (user_role_ids & allowed_role_ids):
            no_perm_embed = build_error(
                "❌ Недостаточно прав",
                "Эта команда доступна только лидеру, зам.лидера или администратору клана.",
            )
            try:
                await ctx.send(embed=no_perm_embed, delete_after=10)
            except discord.HTTPException:
                pass
            return

        embed = build_rules_embed(config)

        try:
            msg = await ctx.send(embed=embed)
        except discord.HTTPException as e:
            log.warning("Не удалось отправить правила: %s", e)
            await ctx.send("❌ Ошибка при отправке правил.")
            return

        # Пытаемся закрепить сообщение
        try:
            await msg.pin()
            pinned_embed = build_success(
                "✅ Правила установлены",
                f"Сообщение с правилами закреплено в {ctx.channel.mention}.\n"
                f"Если закреп не сработал — закрепите вручную.",
            )
        except discord.Forbidden:
            pinned_embed = build_warning(
                "⚠️ Правила отправлены, но без закрепления",
                f"У бота нет прав на закрепление сообщений. "
                f"Закрепите сообщение вручную в {ctx.channel.mention}.",
            )
        except discord.HTTPException as e:
            log.warning("Не удалось закрепить правила: %s", e)
            pinned_embed = build_warning(
                "⚠️ Правила отправлены, но без закрепления",
                f"Закрепите сообщение вручную в {ctx.channel.mention}.",
            )

        try:
            await ctx.send(embed=pinned_embed, delete_after=15)
        except discord.HTTPException:
            pass


# ============================================================================
# Регистрация
# ============================================================================

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RulesCog(bot))
    log.info("Cog 'rules' загружен")
