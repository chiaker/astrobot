from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import User

BONUS_QUESTIONS = 2

_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")


def generate_code() -> str:
    return uuid4().hex[:8].upper()


def parse_start_arg(text: str | None) -> str | None:
    """Pull the ref_XXXXXXXX param out of '/start ref_XXXXXXXX' or '/start XXXXXXXX'."""
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    if arg.startswith("ref_"):
        arg = arg[4:]
    arg = arg.upper()
    if _CODE_RE.fullmatch(arg):
        return arg
    return None


async def try_apply_referral(
    session: AsyncSession,
    invitee: User,
    code: str,
) -> bool:
    """Apply referral code to invitee. Returns True on success."""
    if invitee.referred_by_user_id is not None:
        return False
    if invitee.referral_code == code:
        return False

    inviter = await session.scalar(select(User).where(User.referral_code == code))
    if inviter is None or inviter.id == invitee.id:
        return False

    invitee.referred_by_user_id = inviter.id
    invitee.bonus_questions = (invitee.bonus_questions or 0) + BONUS_QUESTIONS
    inviter.bonus_questions = (inviter.bonus_questions or 0) + BONUS_QUESTIONS
    return True


async def build_share_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start=ref_{code}"
