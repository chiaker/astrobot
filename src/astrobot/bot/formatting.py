from __future__ import annotations

import html
import re

_FENCE_RE = re.compile(r"```(?:\w+)?\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", re.MULTILINE)
_BOLD_STAR_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_BOLD_UNDER_RE = re.compile(r"__([^_\n]+?)__")
_ITALIC_STAR_RE = re.compile(r"(?<![\*\w])\*([^*\n]+?)\*(?![\*\w])")
_ITALIC_UNDER_RE = re.compile(r"(?<![_\w])_([^_\n]+?)_(?![_\w])")
_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_HRULE_RE = re.compile(r"^\s{0,3}[-*_]{3,}\s*$", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_TRAILING_BLANKS = re.compile(r"\n{3,}")


def md_to_telegram_html(text: str) -> str:
    """Convert LLM-style markdown into Telegram-safe HTML.

    Telegram supports a tiny subset (<b>,<i>,<u>,<s>,<code>,<pre>,<a>),
    so we strip everything else and turn headings/bullets into bold/•.
    """
    if not text:
        return text

    code_blocks: list[str] = []

    def _stash_block(m: re.Match[str]) -> str:
        code_blocks.append(html.escape(m.group(1)))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    inline_codes: list[str] = []

    def _stash_inline(m: re.Match[str]) -> str:
        inline_codes.append(html.escape(m.group(1)))
        return f"\x00INLINE{len(inline_codes) - 1}\x00"

    s = _FENCE_RE.sub(_stash_block, text)
    s = _INLINE_CODE_RE.sub(_stash_inline, s)

    s = _LINK_RE.sub(lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', s)
    s = _HRULE_RE.sub("", s)
    s = _HEADING_RE.sub(r"<b>\1</b>", s)
    s = _BOLD_STAR_RE.sub(r"<b>\1</b>", s)
    s = _BOLD_UNDER_RE.sub(r"<b>\1</b>", s)
    s = _ITALIC_STAR_RE.sub(r"<i>\1</i>", s)
    s = _ITALIC_UNDER_RE.sub(r"<i>\1</i>", s)
    s = _BULLET_RE.sub("• ", s)
    s = _TRAILING_BLANKS.sub("\n\n", s)

    for i, code in enumerate(inline_codes):
        s = s.replace(f"\x00INLINE{i}\x00", f"<code>{code}</code>")
    for i, block in enumerate(code_blocks):
        s = s.replace(f"\x00CODEBLOCK{i}\x00", f"<pre>{block}</pre>")

    return s.strip()
