from __future__ import annotations

from aiogram import Router

# The brief/full ("кратко/подробно") toggle was removed — responses are always
# sent in the detailed version. This router is kept (empty) so the dispatcher
# registration stays valid; it can be dropped entirely in a later cleanup.
router = Router(name="response_toggle")
