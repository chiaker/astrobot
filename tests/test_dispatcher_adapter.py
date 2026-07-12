from __future__ import annotations

from aiogram import Dispatcher

from astrobot.bot.dispatcher import _adapt_ctx_handlers


async def test_ctx_first_handlers_no_positional_collision():
    """aiogram binds the update to the first positional param. Our migrated
    handlers take `ctx` there while the middleware also injects `ctx` via data —
    which used to raise `TypeError: got multiple values for 'ctx'`. The adapter
    must drop the positional event for ctx-style handlers, while leaving raw-event
    handlers (which take `message`) alone.
    """
    dp = Dispatcher()
    seen: dict[str, tuple] = {}

    @dp.message()
    async def ctx_msg(ctx, session, user):  # migrated: no raw event param
        seen["ctx_msg"] = (ctx, session, user)

    @dp.message()
    async def raw_msg(message, user):  # non-migrated: still wants the event
        seen["raw_msg"] = (message, user)

    @dp.callback_query()
    async def ctx_cb(ctx, user):
        seen["ctx_cb"] = (ctx, user)

    _adapt_ctx_handlers(dp)

    event = object()  # stand-in for the aiogram update, passed positionally
    msg_handlers = dp.observers["message"].handlers

    # ctx-first handler: no collision, event dropped, data passed by keyword.
    await msg_handlers[0].call(event, ctx="CTX", session="S", user="U")
    assert seen["ctx_msg"] == ("CTX", "S", "U")

    # raw handler untouched: event still fills the `message` param positionally.
    await msg_handlers[1].call(event, user="U")
    assert seen["raw_msg"] == (event, "U")

    await dp.observers["callback_query"].handlers[0].call(event, ctx="CTX", user="U")
    assert seen["ctx_cb"] == ("CTX", "U")
