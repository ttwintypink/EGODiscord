"""
utils/transcripts.py — Генерация HTML-транскриптов в стиле TicketTool.

Создаёт красивый статичный HTML-файл с:
    - Бренд-баром EGO сверху
    - Шапкой с информацией о тикете (ID канала, пользователь, даты, тип)
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
    "main_dark": "#4752C4",
    "success": "#57F287",
    "error": "#ED4245",
    "warning": "#FEE75C",
    "bg": "#1e1f22",
    "bg_card": "#2b2d31",
    "bg_hover": "#313338",
    "bg_deep": "#18191c",
    "text_primary": "#f2f3f5",
    "text_muted": "#949ba4",
    "text_dim": "#80848e",
    "border": "#1e1f22",
    "border_light": "#3f4147",
    "accent_blue": "#5865F2",
    "shadow_main": "rgba(88, 101, 242, 0.4)",
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
    import re
    s = _esc(text)
    # bold
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    # italic (single *)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    # underline
    s = re.sub(r"__([^_]+)__", r"<u>\1</u>", s)
    # code
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # spoiler
    s = re.sub(r"\|\|([^|]+)\|\|", r'<span class="spoiler">\1</span>', s)
    # URLs
    s = re.sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        s,
    )
    # newlines
    s = s.replace("\n", "<br>")
    return s


def _message_html(author_name: str, author_id: int, content: str,
                  created_at: str, avatar_url: str | None = None,
                  role_color: str | None = None,
                  bot: bool = False) -> str:
    avatar = avatar_url or "https://cdn.discordapp.com/embed/avatars/0.png"
    name_color = role_color or CSS_COLORS["text_primary"]
    bot_badge = '<span class="bot-badge">BOT</span>' if bot else ""
    return f"""
    <div class="message">
        <img class="avatar" src="{_esc(avatar)}" alt="avatar" loading="lazy">
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
        padding: 0;
        line-height: 1.5;
    }}
    /* Бренд-бар EGO сверху */
    .brand-bar {{
        background: linear-gradient(135deg, {CSS_COLORS['main_dark']} 0%, {CSS_COLORS['main']} 100%);
        padding: 14px 24px;
        display: flex;
        align-items: center;
        gap: 12px;
        box-shadow: 0 4px 12px {CSS_COLORS['shadow_main']};
    }}
    .brand-bar .logo {{
        width: 36px;
        height: 36px;
        background: #fff;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 18px;
        font-weight: 700;
        color: {CSS_COLORS['main']};
    }}
    .brand-bar .brand-text {{
        color: #fff;
        font-size: 18px;
        font-weight: 600;
        letter-spacing: 0.3px;
    }}
    .brand-bar .brand-sub {{
        color: rgba(255, 255, 255, 0.7);
        font-size: 12px;
        margin-left: auto;
    }}
    .transcript-container {{
        max-width: 880px;
        margin: 24px auto;
        background: {CSS_COLORS['bg_card']};
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid {CSS_COLORS['border']};
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
    }}
    .header {{
        background: {CSS_COLORS['bg_hover']};
        padding: 24px 28px;
        border-bottom: 1px solid {CSS_COLORS['border']};
    }}
    .header h1 {{
        font-size: 22px;
        color: {CSS_COLORS['text_primary']};
        margin-bottom: 4px;
        font-weight: 700;
    }}
    .header .subtitle {{
        color: {CSS_COLORS['text_muted']};
        font-size: 14px;
        margin-bottom: 16px;
    }}
    .header .meta {{
        color: {CSS_COLORS['text_muted']};
        font-size: 13px;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 8px 16px;
    }}
    .header .meta-item {{
        display: inline-flex;
        align-items: center;
        padding: 6px 10px;
        background: {CSS_COLORS['bg_deep']};
        border-radius: 6px;
        font-size: 12px;
    }}
    .header .meta-item::before {{
        content: '';
        display: inline-block;
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: {CSS_COLORS['main']};
        margin-right: 8px;
        flex-shrink: 0;
    }}
    .header .meta-item strong {{
        color: {CSS_COLORS['text_primary']};
        margin-left: 6px;
        font-weight: 600;
    }}
    .messages-list {{
        padding: 12px 0;
    }}
    .message {{
        display: flex;
        padding: 8px 28px 8px 20px;
        gap: 14px;
        transition: background 0.15s;
    }}
    .message:hover {{
        background: {CSS_COLORS['bg_hover']};
    }}
    .avatar {{
        width: 42px;
        height: 42px;
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
        margin-bottom: 3px;
        flex-wrap: wrap;
    }}
    .username {{
        font-weight: 600;
        font-size: 15px;
    }}
    .bot-badge {{
        background: {CSS_COLORS['main']};
        color: #fff;
        font-size: 10px;
        padding: 1px 5px;
        border-radius: 3px;
        font-weight: 600;
        position: relative;
        top: -2px;
    }}
    .timestamp {{
        color: {CSS_COLORS['text_dim']};
        font-size: 11px;
    }}
    .content {{
        color: {CSS_COLORS['text_primary']};
        font-size: 15px;
        word-wrap: break-word;
        overflow-wrap: break-word;
    }}
    .content code {{
        background: {CSS_COLORS['bg_deep']};
        padding: 2px 5px;
        border-radius: 4px;
        font-family: 'Consolas', 'Monaco', monospace;
        font-size: 13px;
        color: {CSS_COLORS['warning']};
    }}
    .content strong {{ font-weight: 700; }}
    .content a {{ color: #00a8fc; text-decoration: none; }}
    .content a:hover {{ text-decoration: underline; }}
    .spoiler {{
        background: {CSS_COLORS['bg_deep']};
        color: transparent;
        border-radius: 3px;
        padding: 0 3px;
        cursor: pointer;
        transition: color 0.2s;
    }}
    .spoiler:hover {{
        background: {CSS_COLORS['bg']};
        color: {CSS_COLORS['text_primary']};
    }}
    .empty {{
        padding: 48px 24px;
        text-align: center;
        color: {CSS_COLORS['text_muted']};
        font-size: 14px;
    }}
    .footer {{
        padding: 16px 28px;
        background: {CSS_COLORS['bg_hover']};
        border-top: 1px solid {CSS_COLORS['border']};
        color: {CSS_COLORS['text_muted']};
        font-size: 12px;
        text-align: center;
    }}
    .footer a {{ color: #00a8fc; text-decoration: none; }}
    .footer strong {{ color: {CSS_COLORS['text_primary']}; }}
    .badge {{
        display: inline-block;
        padding: 3px 8px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 600;
        margin-right: 6px;
    }}
    .badge-clan {{
        background: {CSS_COLORS['success']};
        color: {CSS_COLORS['bg_deep']};
    }}
    .badge-mod {{
        background: {CSS_COLORS['warning']};
        color: {CSS_COLORS['bg_deep']};
    }}
    .badge-pirate {{
        background: #E67E22;
        color: #fff;
    }}
    /* Баннер пиратки в шапке транскрипта */
    .pirate-banner {{
        background: linear-gradient(135deg, #E67E22 0%, #D35400 100%);
        color: #fff;
        padding: 14px 28px;
        border-bottom: 1px solid #C0392B;
        font-size: 14px;
        line-height: 1.5;
    }}
    .pirate-banner strong {{ font-weight: 700; }}
    .pirate-banner .pirate-title {{
        font-size: 16px;
        font-weight: 700;
        margin-bottom: 6px;
    }}
    .pirate-banner .pirate-evidence {{
        margin-top: 8px;
        padding-left: 18px;
    }}
    .pirate-banner .pirate-evidence li {{
        margin: 3px 0;
    }}
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
    is_pirate: bool = False,
    pirate_evidence: list[str] | None = None,
) -> str:
    """
    messages: итерируемый объект с dict-полями:
        author_name, author_id, content, created_at (str),
        avatar_url (str | None), role_color (str | None), bot (bool)

    is_pirate: если True — в шапку транскрипта добавляется оранжевый
        баннер с предупреждением о пиратке (Spacewar).
    pirate_evidence: список строк-признаков пиратки.
    """
    closed_at = closed_at or now_msk()
    type_label = "Клан" if ticket_type == "clan" else "Модерация"
    badge_class = "badge-clan" if ticket_type == "clan" else "badge-mod"
    badge_html = f'<span class="badge {badge_class}">{_esc(type_label)}</span>'
    if is_pirate:
        badge_html += ' <span class="badge badge-pirate">🏴‍☠️ Пират (Spacewar)</span>'

    messages_list = list(messages)
    messages_html = []
    for m in messages_list:
        messages_html.append(_message_html(
            author_name=m.get("author_name", "Unknown"),
            author_id=m.get("author_id", 0),
            content=m.get("content", "") or "",
            created_at=m.get("created_at", ""),
            avatar_url=m.get("avatar_url"),
            role_color=m.get("role_color"),
            bot=m.get("bot", False),
        ))

    messages_block = (
        "".join(messages_html) if messages_html
        else '<div class="empty">📭 Сообщений не найдено</div>'
    )

    # Баннер пиратки — отдельный блок в шапке, заметный оранжевый.
    pirate_banner_html = ""
    if is_pirate:
        evidence_items = ""
        if pirate_evidence:
            items = "".join(f"<li>{_esc(e)}</li>" for e in pirate_evidence[:5])
            evidence_items = f'<ul class="pirate-evidence">{items}</ul>'
        pirate_banner_html = f"""
        <div class="pirate-banner">
            <div class="pirate-title">🏴‍☠️ Обнаружена пиратская версия Rust!</div>
            <div>Кандидат играет через <strong>Spacewar</strong> (тестовое приложение Steam, которое пираты используют для запуска Rust без лицензии).</div>
            {evidence_items}
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Транскрипт тикета — {_esc(channel_name)}</title>
    <style>{_build_css()}</style>
</head>
<body>
    <div class="brand-bar">
        <div class="logo">E</div>
        <div class="brand-text">EGODiscord System</div>
        <div class="brand-sub">Ticket Transcript • {badge_html}</div>
    </div>
    <div class="transcript-container">
        {pirate_banner_html}
        <div class="header">
            <h1>🎫 Транскрипт тикета — {_esc(channel_name)}</h1>
            <div class="subtitle">Полная история переписки из закрытого тикета</div>
            <div class="meta">
                <span class="meta-item">Канал: <strong>#{_esc(channel_name)}</strong></span>
                <span class="meta-item">ID канала: <strong>{channel_id}</strong></span>
                <span class="meta-item">Кандидат: <strong>{_esc(user_name)} ({user_id})</strong></span>
                <span class="meta-item">Тип: <strong>{_esc(type_label)}</strong></span>
                <span class="meta-item">Создан: <strong>{_esc(created_at.strftime('%d.%m.%Y %H:%M:%S МСК'))}</strong></span>
                <span class="meta-item">Закрыт: <strong>{_esc(closed_at.strftime('%d.%m.%Y %H:%M:%S МСК'))}</strong></span>
                <span class="meta-item">Сообщений: <strong>{len(messages_list)}</strong></span>
            </div>
        </div>
        <div class="messages-list">
            {messages_block}
        </div>
        <div class="footer">
            <strong>EGODiscord System</strong> — транскрипт сгенерирован {_esc(msk_timestamp())}<br>
            Файл предназначен только для администрации клана EGO.
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
