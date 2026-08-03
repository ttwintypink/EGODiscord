"""
cogs/developer.py — Скрытые команды разработчика:
    .setup       — установить панель тикетов (только developer_id)
    .call <text> — отправить кандидату сообщение в ЛС
                   (работает только в каналах тикетов)
    .voice       — альтернатива кнопке обзвона для офицеров
"""

from __future__ import annotations

import logging

import discord
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


class Developer(commands.Cog):
    """Скрытые команды разработчика."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _config(self) -> dict:
        return getattr(self.bot, "_config", None) or {}

    def _is_dev(self, user: discord.abc.User) -> bool:
        return user.id == self._config().get("developer_id", 0)

    # Примечание: команда .setup определена в cogs/tickets.py — тут не дублируем.

    # --- .call <text> --------------------------------------------------------
    @commands.command(name="call", hidden=True)
    @commands.guild_only()
    async def call(self, ctx: commands.Context, *, text: str):
        """
        Отправляет кандидату в ЛС Embed от имени разработчика.
        Работает ТОЛЬКО в каналах тикетов.
        """
        if not self._is_dev(ctx.author):
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return

        if not text:
            await ctx.send(embed=build_error(description="Укажите текст сообщения: `.call <текст>`"),
                           delete_after=5)
            return

        # Проверяем, что команда вызвана в канале тикета
        ticket = await database.ticket_get(ctx.channel.id)
        if ticket is None:
            await ctx.send(embed=build_error(
                description="Эту команду можно использовать только внутри канала тикета."
            ), delete_after=5)
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return

        guild = ctx.guild
        candidate = guild.get_member(ticket["user_id"])
        if candidate is None:
            await ctx.send(embed=build_error(description="Кандидат не найден на сервере."),
                           delete_after=5)
            return

        # Формируем Embed для ЛС — премиум-стиль
        dev_member = ctx.author
        dm_embed = discord.Embed(
            title="⚠️ Сообщение от разработчика EGO",
            description=(
                f"## 📩 Вам пришло сообщение\n\n"
                f"**От:** {dev_member.mention}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{text}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0xFEE75C,
            timestamp=embeds.now_msk(),
        )
        dm_embed.add_field(
            name="🎫 Тикет",
            value=ctx.channel.mention,
            inline=True,
        )
        dm_embed.add_field(
            name="⏱️ Время",
            value=msk_timestamp(),
            inline=True,
        )
        dm_embed.set_thumbnail(url=dev_member.display_avatar.url)
        dm_embed.set_footer(text="EGODiscord System • Developer Message")

        try:
            dm = await candidate.create_dm()
            await dm.send(embed=dm_embed)
            sent = True
        except discord.Forbidden:
            sent = False
        except discord.HTTPException as e:
            log.warning("Не удалось отправить ЛС: %s", e)
            sent = False

        if sent:
            await ctx.send(embed=build_success(
                title="✅ Сообщение отправлено",
                description=f"Сообщение доставлено в ЛС кандидату {candidate.mention}.",
            ))
        else:
            await ctx.send(embed=build_warning(
                title="⚠️ ЛС недоступно",
                description=f"Не удалось отправить сообщение в ЛС кандидату {candidate.mention}. "
                            f"Возможно, его ЛС закрыты.",
            ))

        # Удаляем сообщение с командой (чтобы не засорять тикет)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # --- .voice --------------------------------------------------------------
    @commands.command(name="voice", hidden=False)
    @commands.guild_only()
    async def voice(self, ctx: commands.Context):
        """
        Альтернатива кнопке обзвона для офицеров.
        Создаёт голосовой канал для текущего тикета.
        """
        config = self._config()
        if not _is_staff(ctx.author, config):
            await ctx.send(embed=embeds.error_no_permission(), delete_after=5)
            return

        ticket = await database.ticket_get(ctx.channel.id)
        if ticket is None:
            await ctx.send(embed=build_error(
                description="Эту команду можно использовать только внутри канала тикета."
            ), delete_after=5)
            return

        # Создаём фейковый interaction-подобный объект для переиспользования логики
        class _FakeInteraction:
            def __init__(self, channel, user, guild, message):
                self.channel = channel
                self.user = user
                self.guild = guild
                self.message = message
                self._responded = False

            async def response_send(self, *args, **kwargs):
                self._responded = True
                return await self.channel.send(*args, **kwargs)

            class _Resp:
                @staticmethod
                async def send_message(*args, **kwargs):
                    pass

                @staticmethod
                def is_done():
                    return False

            response = _Resp()

            async def followup_send(self, *args, **kwargs):
                return await self.channel.send(*args, **kwargs)

        from cogs.ticket_control import _create_voice_channel
        fake = _FakeInteraction(ctx.channel, ctx.author, ctx.guild, None)

        # Обёртка: _create_voice_channel ожидает interaction.response.is_done()
        class _WrappedInteraction:
            def __init__(self, base):
                self._base = base
                self.channel = base.channel
                self.user = base.user
                self.guild = base.guild
                self.message = base.message

                class _Resp:
                    done = False
                    async def send_message(self_inner, *args, **kwargs):
                        self_inner.done = True
                        return await ctx.channel.send(*args, **kwargs)
                    def is_done(self_inner):
                        return self_inner.done

                self.response = _Resp()

            async def followup_send(self, *args, **kwargs):
                return await ctx.channel.send(*args, **kwargs)

        wrapped = _WrappedInteraction(fake)
        await _create_voice_channel(wrapped, config, ticket)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Developer(bot))
