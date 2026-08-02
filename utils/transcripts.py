"""
utils/transcripts.py — Генерация HTML-транскриптов в стиле TicketTool.

Создаёт красивый статичный HTML-файл с:
    - Шапкой с информацией о тикете (ID канала, пользователь, даты)
    - Лентой сообщений с аватарками, никами, цветами ролей, timestamp
    - Подсветкой кода и форматированием Markdown (минимально)
    - Встроенным CSS (без внешних зависимостей)

Также содержит функцию сохранения закреплённой анкеты в TXT.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Iterable

from utils.embeds import msk_timestamp, now_msk

# Константы цветов в CSS (соответствуют палитре)
CSS_COLORS = {
    "main": "#5865F2",
    "success": "#57F287",
    "error": "#ED4245",
    "warning": "#FEE75C",
    "bg": "#313338",
    "bg_card": "#2b2d31",
    "bg_hover": "#1e1f22",
    "text_primary": "#f2f3f5",
    "text_muted": "#949ba4",
    "border": "#1e1f22",
    "accent_blue": "#5865F2",
}


def _esc(text: str | None) -> str:
    """HTML-экранирование."""
    if text is None:
        return ""
    return html.escape(str(text))


def _format_markdown_lite(text: str) -> str:
    """
    Минимальное форматирование:
        **bold** -> <strong>
        `code`   -> <code>
        __underline__
        ||spoiler|| -> <span class="spoiler">
        Ссылки кликабельными
    """
    if not text:
        return ""
    s = _esc(text)
    # bold
    s = __re_sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    # italic (single *)
    s = __re_sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    # underline
    s = __re_sub(r"__([^_]+)__", r"<u>\1</u>", s)
    # code
    s = __re_sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # spoiler
    s = __re_sub(r"\|\|([^|]+)\|\|", r'<span class="spoiler">\1</span>', s)
    # URLs
    s = __re_sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        s,
    )
    # newlines
    s = s.replace("\n", "<br>")
    return s


def __re_sub(pattern: str, repl: str, string: str) -> str:
    import re
    return re.sub(pattern, repl, string)


def _message_html(author_name: str, author_id: int, content: str,
                  created_at: str, avatar_url: str | None = None,
                  role_color: str | None = None,
                  bot: bool = False) -> str:
    avatar = avatar_url or "https://cdn.discordapp.com/embed/avatars/0.png"
    name_color = role_color or CSS_COLORS["text_primary"]
    bot_badge = '<span class="bot-badge">BOT</span>' if bot else ""
    return f"""
    <div class="message">
        <img class="avatar" src="{_esc(avatar)}" alt="avatar">
        <div class="content-wrap">
            <div class="header-line">
                <span class="username" style="color: {_esc(name_color)}">{_esc(author_name)}</span>
                {bot_badge}
                <span class="timestamp">{_esc(created_at)}</span>
            </div>
            <div class="content">{_format_markdown_lite(content)}</div>
        </div>
    </div>
    """


def _build_css() -> str:
    return f"""
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: 'gg sans', 'Noto Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        background: {CSS_COLORS['bg']};
        color: {CSS_COLORS['text_primary']};
        padding: 16px;
        line-height: 1.4;
    }}
    .transcript-container {{
        max-width: 800px;
        margin: 0 auto;
        background: {CSS_COLORS['bg_card']};
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid {CSS_COLORS['border']};
    }}
    .header {{
        background: {CSS_COLORS['bg_hover']};
        padding: 20px 24px;
        border-bottom: 1px solid {CSS_COLORS['border']};
    }}
    .header h1 {{
        font-size: 20px;
        color: {CSS_COLORS['text_primary']};
        margin-bottom: 8px;
    }}
    .header .meta {{
        color: {CSS_COLORS['text_muted']};
        font-size: 13px;
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
    }}
    .header .meta-item {{
        display: inline-flex;
        align-items: center;
    }}
    .header .meta-item::before {{
        content: '';
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: {CSS_COLORS['main']};
        margin-right: 6px;
    }}
    .messages-list {{
        padding: 8px 0;
    }}
    .message {{
        display: flex;
        padding: 6px 24px 6px 16px;
        gap: 12px;
        transition: background 0.1s;
    }}
    .message:hover {{
        background: {CSS_COLORS['bg_hover']};
    }}
    .avatar {{
        width: 40px;
        height: 40px;
        border-radius: 50%;
        flex-shrink: 0;
        margin-top: 2px;
    }}
    .content-wrap {{
        flex: 1;
        min-width: 0;
    }}
    .header-line {{
        display: flex;
        align-items: baseline;
        gap: 8px;
        margin-bottom: 2px;
    }}
    .username {{
        font-weight: 600;
        font-size: 15px;
    }}
    .bot-badge {{
        background: {CSS_COLORS['main']};
        color: #fff;
        font-size: 10px;
        padding: 1px 4px;
        border-radius: 3px;
        font-weight: 600;
        position: relative;
        top: -2px;
    }}
    .timestamp {{
        color: {CSS_COLORS['text_muted']};
        font-size: 11px;
    }}
    .content {{
        color: {CSS_COLORS['text_primary']};
        font-size: 15px;
        word-wrap: break-word;
        overflow-wrap: break-word;
    }}
    .content code {{
        background: {CSS_COLORS['bg_hover']};
        padding: 1px 4px;
        border-radius: 3px;
        font-family: 'Consolas', 'Monaco', monospace;
        font-size: 13px;
    }}
    .content strong {{ font-weight: 700; }}
    .content a {{ color: #00a8fc; text-decoration: none; }}
    .content a:hover {{ text-decoration: underline; }}
    .spoiler {{
        background: #1e1f22;
        color: transparent;
        border-radius: 3px;
        padding: 0 2px;
        cursor: pointer;
    }}
    .spoiler:hover {{ background: #111214; color: {CSS_COLORS['text_primary']}; }}
    .footer {{
        padding: 12px 24px;
        background: {CSS_COLORS['bg_hover']};
        border-top: 1px solid {CSS_COLORS['border']};
        color: {CSS_COLORS['text_muted']};
        font-size: 12px;
        text-align: center;
    }}
    .footer a {{ color: #00a8fc; text-decoration: none; }}
    """


def generate_html_transcript(
    *,
    channel_name: str,
    channel_id: int,
    user_id: int,
    user_name: str,
    ticket_type: str,
    created_at: datetime,
    closed_at: datetime | None = None,
    messages: Iterable[dict],
) -> str:
    """
    messages: итерируемый объект с dict-полями:
        author_name, author_id, content, created_at (str),
        avatar_url (str | None), role_color (str | None), bot (bool)
    """
    closed_at = closed_at or now_msk()
    type_label = "Клан" if ticket_type == "clan" else "Модерация"

    messages_html = []
    for m in messages:
        messages_html.append(_message_html(
            author_name=m.get("author_name", "Unknown"),
            author_id=m.get("author_id", 0),
            content=m.get("content", "") or "",
            created_at=m.get("created_at", ""),
            avatar_url=m.get("avatar_url"),
            role_color=m.get("role_color"),
            bot=m.get("bot", False),
        ))

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Транскрипт тикета — {_esc(channel_name)}</title>
    <style>{_build_css()}</style>
</head>
<body>
    <div class="transcript-container">
        <div class="header">
            <h1>🎫 Транскрипт тикета — {_esc(channel_name)}</h1>
            <div class="meta">
                <span class="meta-item">Канал: <strong style="margin-left:4px">#{_esc(channel_name)}</strong></span>
                <span class="meta-item">ID канала: <strong style="margin-left:4px">{channel_id}</strong></span>
                <span class="meta-item">Кандидат: <strong style="margin-left:4px">{_esc(user_name)} ({user_id})</strong></span>
                <span class="meta-item">Тип: <strong style="margin-left:4px">{type_label}</strong></span>
                <span class="meta-item">Создан: <strong style="margin-left:4px">{_esc(created_at.strftime('%d.%m.%Y %H:%M:%S МСК'))}</strong></span>
                <span class="meta-item">Закрыт: <strong style="margin-left:4px">{_esc(closed_at.strftime('%d.%m.%Y %H:%M:%S МСК'))}</strong></span>
            </div>
        </div>
        <div class="messages-list">
            {''.join(messages_html) if messages_html else '<div style="padding:24px;text-align:center;color:#949ba4">Сообщений не найдено</div>'}
        </div>
        <div class="footer">
            EGODiscord System — транскрипт сгенерирован {_esc(msk_timestamp())}
        </div>
    </div>
</body>
</html>
"""


def save_form_txt(form_text: str, channel_id: int, user_name: str) -> str:
    """Сохраняет текст анкеты в TXT-файл и возвращает путь."""
    import os
    os.makedirs("transcripts", exist_ok=True)
    safe_name = "".join(c for c in user_name if c.isalnum() or c in "_-")[:32] or "user"
    path = f"transcripts/form_{channel_id}_{safe_name}.txt"
    header = (
        f"========================================\n"
        f"  EGODiscord — Анкета кандидата\n"
        f"========================================\n"
        f"Канал:    #{channel_id}\n"
        f"Кандидат: {user_name}\n"
        f"Дата:     {msk_timestamp()}\n"
        f"----------------------------------------\n\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + form_text + "\n")
    return path


def save_html(html_content: str, channel_id: int, user_name: str) -> str:
    """Сохраняет HTML в файл и возвращает путь."""
    import os
    os.makedirs("transcripts", exist_ok=True)
    safe_name = "".join(c for c in user_name if c.isalnum() or c in "_-")[:32] or "user"
    path = f"transcripts/transcript_{channel_id}_{safe_name}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return path
