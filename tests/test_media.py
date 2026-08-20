from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrobot.bot.platform import Media, media_kind
from astrobot.bot.platform.telegram import TelegramBot

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
WEBP_STILL = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32
WEBP_ANIM = b"RIFF\x00\x00\x00\x00WEBPVP8X" + bytes([0, 0, 0, 0, 0x02]) + b"\x00" * 32


# ─── media_kind: bytes win over the filename ───────────────────────────────────

def test_still_images_are_photos():
    assert media_kind(PNG) == (True, "image.png")
    assert media_kind(JPEG) == (True, "image.jpg")
    assert media_kind(WEBP_STILL) == (True, "image.webp")


def test_moving_images_are_animations():
    assert media_kind(GIF) == (False, "animation.gif")
    assert media_kind(MP4) == (False, "animation.mp4")
    assert media_kind(WEBP_ANIM) == (False, "animation.webp")


def test_bytes_beat_a_misleading_name():
    # The usual case: a downloaded "gif" that is really an MP4, and a PNG someone
    # saved as .gif. The extension must follow the bytes, or Telegram sends a file.
    assert media_kind(MP4, "loop.gif") == (False, "animation.mp4")
    assert media_kind(PNG, "stars.gif") == (True, "image.png")


def test_without_bytes_the_extension_decides():
    # A pasted file_id or URL has no bytes to sniff.
    assert media_kind(None, "https://cdn.io/a/stars.png") == (True, "https://cdn.io/a/stars.png")
    assert media_kind(None, "https://cdn.io/a/loop.mp4?v=2")[0] is False
    assert media_kind(None, "AgACAgIAAxkBAA") == (False, "animation.mp4")


def test_media_is_photo_property():
    assert Media.from_bytes(PNG, "whatever.gif").is_photo
    assert not Media.from_bytes(MP4, "whatever.gif").is_photo
    assert Media.from_url("https://cdn.io/pic.jpeg").is_photo
    assert not Media.from_file_id("AgACAgIAAxkBAA").is_photo


# ─── the adapter routes stills to sendPhoto ────────────────────────────────────

def _fake_bot():
    bot = AsyncMock()
    bot.send_photo.return_value = SimpleNamespace(
        message_id=1, photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]
    )
    bot.send_animation.return_value = SimpleNamespace(
        message_id=2, animation=SimpleNamespace(file_id="anim"), video=None
    )
    return bot


async def test_send_animation_on_a_still_uses_send_photo():
    # Telegram rejects sendAnimation for a PNG, so the adapter reroutes instead of
    # every caller having to know what it is holding.
    bot = _fake_bot()
    sent = await TelegramBot(bot).send_animation(1, Media.from_bytes(PNG, "stars.png"), "cap")
    bot.send_animation.assert_not_awaited()
    bot.send_photo.assert_awaited_once()
    assert sent.file_id == "big"  # largest size, cached so a broadcast re-uses it


async def test_cached_photo_id_is_still_recognised_as_a_photo():
    # After the first send only the file_id is stored — no bytes left to sniff, so
    # the original filename has to carry the type or the reuse would be rejected.
    from astrobot.db.models import BroadcastVariant
    from astrobot.scheduler import _send_broadcast_variant

    pbot = AsyncMock()
    pbot.send_animation.return_value = SimpleNamespace(message_id=1, file_id=None)
    variant = BroadcastVariant(
        id=3, text="привет", buttons=[], animation="PHOTO_ID", animation_name="stars.png"
    )
    await _send_broadcast_variant(pbot, 42, variant)
    assert pbot.send_animation.await_args.args[1].is_photo

    # And when the stored bytes are still around they overrule a misleading name,
    # so an old upload saved as "clip.png" that is really an MP4 keeps working.
    pbot.send_animation.reset_mock()
    mislabeled = BroadcastVariant(
        id=4, text="", buttons=[], animation="ANIM_ID",
        animation_name="clip.png", animation_data=MP4,
    )
    await _send_broadcast_variant(pbot, 42, mislabeled)
    assert not pbot.send_animation.await_args.args[1].is_photo


async def test_real_animations_still_go_through_send_animation():
    bot = _fake_bot()
    sent = await TelegramBot(bot).send_animation(1, Media.from_bytes(MP4, "loop.mp4"), "cap")
    bot.send_photo.assert_not_awaited()
    bot.send_animation.assert_awaited_once()
    assert sent.file_id == "anim"
