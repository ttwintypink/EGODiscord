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

        # Обёртка: _create_voice_channel ожидает interaction с методами
        # response.send_message / response.edit_message / response.is_done /
        # followup.send / interaction.message.edit. Команда .voice вызывается
        # из контекста commands.Context (а не из interaction), поэтому делаем
        # адаптер, который редиректит все вызовы на ctx.channel.send.
        class _WrappedInteraction:
            def __init__(self, base):
                self._base = base
                self.channel = base.channel
                self.user = base.user
                self.guild = base.guild
                # ctx.message существует всегда
                self.message = getattr(base, "message", None)

                class _Resp:
                    _done = False
                    async def send_message(self_inner, *args, **kwargs):
                        self_inner._done = True
                        # из ctx.channel.send возвращается Message — Discord.Message
                        return await ctx.channel.send(*args, **kwargs)
                    async def edit_message(self_inner, *args, **kwargs):
                        # Если есть message — редактируем его; иначе шлём новое
                        if self.message is not None:
                            try:
                                return await self.message.edit(*args, **kwargs)
                            except discord.HTTPException:
                                pass
                        return await ctx.channel.send(*args, **kwargs)
                    def is_done(self_inner):
                        return self_inner._done

                self.response = _Resp()

            class _Followup:
                async def send(self_inner, *args, **kwargs):
                    return await ctx.channel.send(*args, **kwargs)

            @property
            def followup(self):
                return self._Followup()

        wrapped = _WrappedInteraction(fake)
        await _create_voice_channel(wrapped, config, ticket)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # --- .ping — проверка задержки -----------------------------------------

    @commands.command(name="ping", hidden=True)
    @commands.guild_only()
    async def ping_cmd(self, ctx: commands.Context):
        """Проверить задержку бота до Discord API (только dev)."""
        if not self._is_dev(ctx.author):
            return

        latency = self.bot.latency * 1000 if self.bot.latency > 0 else 0
        emoji = "🟢" if latency < 100 else "🟡" if latency < 300 else "🔴"
        color = (
            embeds.COLOR_SUCCESS if latency < 100
            else embeds.COLOR_WARNING if latency < 300
            else embeds.COLOR_ERROR
        )

        embed = discord.Embed(
            title="🏓 Pong!",
            description=(
                f"## {emoji} Задержка до Discord API\n\n"
                f"```fix\n{latency:.0f} ms\n```\n"
                f"{'✅ Отличная задержка' if latency < 100 else '⚠️ Нормальная задержка' if latency < 300 else '❌ Высокая задержка'}"
            ),
            color=color,
            timestamp=embeds.now_msk(),
        )
        embed.set_footer(text=f"EGODiscord System • {msk_timestamp()}")
        await ctx.send(embed=embed)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # --- .info — системная информация --------------------------------------

    @commands.command(name="info", hidden=True, aliases=["sysinfo", "система"])
    @commands.guild_only()
    async def info_cmd(self, ctx: commands.Context):
        """Показать системную информацию (только dev)."""
        if not self._is_dev(ctx.author):
            return

        import platform
        import sys
        import time
        from datetime import datetime, timezone, timedelta

        MSK = timezone(timedelta(hours=3))

        latency = self.bot.latency * 1000 if self.bot.latency > 0 else 0

        # Аптайм (через start time бота, сохраняем в атрибуте)
        if not hasattr(self.bot, "_ego_start_time"):
            self.bot._ego_start_time = time.time()
        uptime_sec = time.time() - self.bot._ego_start_time
        if uptime_sec < 60:
            uptime_str = f"{int(uptime_sec)} сек"
        elif uptime_sec < 3600:
            uptime_str = f"{int(uptime_sec // 60)} мин {int(uptime_sec % 60)} сек"
        elif uptime_sec < 86400:
            uptime_str = f"{int(uptime_sec // 3600)} ч {int((uptime_sec % 3600) // 60)} мин"
        else:
            uptime_str = f"{int(uptime_sec // 86400)} д {int((uptime_sec % 86400) // 3600)} ч"

        embed = discord.Embed(
            title="ℹ️ Информация о системе",
            description=(
                f"## 🤖 EGODiscord System\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=embeds.COLOR_MAIN,
            timestamp=embeds.now_msk(),
        )

        embed.add_field(
            name="🐍 Python",
            value=(
                f"```fix\n"
                f"Версия:       {sys.version.split()[0]}\n"
                f"Платформа:    {platform.system()} {platform.release()}\n"
                f"Архитектура:  {platform.machine()}\n"
                f"```"
            ),
            inline=False,
        )

        embed.add_field(
            name="📚 Библиотеки",
            value=(
                f"```fix\n"
                f"discord.py:   {discord.__version__}\n"
                f"```"
            ),
            inline=False,
        )

        embed.add_field(
            name="🤖 Бот",
            value=(
                f"```fix\n"
                f"Пользователь: {self.bot.user}\n"
                f"ID:           {self.bot.user.id}\n"
                f"Серверов:     {len(self.bot.guilds)}\n"
                f"Пользователей: {sum(g.member_count or 0 for g in self.bot.guilds):,}\n"
                f"Задержка:     {latency:.0f} ms\n"
                f"Аптайм:       {uptime_str}\n"
                f"```"
            ),
            inline=False,
        )

        loaded_cogs = list(self.bot.cogs.keys())
        embed.add_field(
            name="📦 Загруженные модули",
            value=f"```fix\n{', '.join(loaded_cogs)}```" if loaded_cogs else "—",
            inline=False,
        )

        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"EGODiscord System • {msk_timestamp()}")
        await ctx.send(embed=embed)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # --- .load / .unload / .reload — управление когами ---------------------

    @commands.command(name="load", hidden=True)
    @commands.guild_only()
    async def load_cmd(self, ctx: commands.Context, cog: str = None):
        """Загрузить ког (только dev)."""
        if not self._is_dev(ctx.author):
            return
        if not cog:
            await ctx.send(embed=embeds.build_error(
                description="Укажите ког: `.load cogs.help`"
            ), delete_after=10)
            return
        try:
            await self.bot.load_extension(cog)
            await ctx.send(embed=build_success(
                title="✅ Ког загружен",
                description=f"Ког `{cog}` успешно загружен.",
            ))
        except Exception as e:
            await ctx.send(embed=embeds.build_error(
                description=f"Не удалось загрузить `{cog}`:\n```\n{type(e).__name__}: {e}\n```"
            ))

    @commands.command(name="unload", hidden=True)
    @commands.guild_only()
    async def unload_cmd(self, ctx: commands.Context, cog: str = None):
        """Выгрузить ког (только dev)."""
        if not self._is_dev(ctx.author):
            return
        if not cog:
            await ctx.send(embed=embeds.build_error(
                description="Укажите ког: `.unload cogs.help`"
            ), delete_after=10)
            return
        try:
            await self.bot.unload_extension(cog)
            await ctx.send(embed=build_success(
                title="✅ Ког выгружен",
                description=f"Ког `{cog}` успешно выгружен.",
            ))
        except Exception as e:
            await ctx.send(embed=embeds.build_error(
                description=f"Не удалось выгрузить `{cog}`:\n```\n{type(e).__name__}: {e}\n```"
            ))

    @commands.command(name="reload", hidden=True, aliases=["rl"])
    @commands.guild_only()
    async def reload_cmd(self, ctx: commands.Context, cog: str = None):
        """Перезагрузить ког (только dev)."""
        if not self._is_dev(ctx.author):
            return
        if not cog:
            await ctx.send(embed=embeds.build_error(
                description="Укажите ког: `.reload cogs.help`"
            ), delete_after=10)
            return
        try:
            await self.bot.reload_extension(cog)
            await ctx.send(embed=build_success(
                title="✅ Ког перезагружен",
                description=f"Ког `{cog}` успешно перезагружен.",
            ))
        except Exception as e:
            await ctx.send(embed=embeds.build_error(
                description=f"Не удалось перезагрузить `{cog}`:\n```\n{type(e).__name__}: {e}\n```"
            ))


async def setup(bot: commands.Bot):
    await bot.add_cog(Developer(bot))
    # Сохраняем время старта для аптайма
    import time
    if not hasattr(bot, "_ego_start_time"):
        bot._ego_start_time = time.time()
