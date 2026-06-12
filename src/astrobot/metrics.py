from __future__ import annotations

from prometheus_client import Counter, Histogram

MESSAGES_TOTAL = Counter(
    "astrobot_messages_total",
    "Telegram messages processed by handler kind and status",
    ["kind", "status"],
)

CALLBACKS_TOTAL = Counter(
    "astrobot_callbacks_total",
    "Telegram callback queries processed",
    ["prefix", "status"],
)

LLM_CALLS_TOTAL = Counter(
    "astrobot_llm_calls_total",
    "LLM API calls",
    ["kind", "model", "status"],
)

LLM_DURATION = Histogram(
    "astrobot_llm_duration_seconds",
    "LLM call duration",
    ["kind", "model"],
    buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180),
)

LLM_TOKENS_TOTAL = Counter(
    "astrobot_llm_tokens_total",
    "LLM tokens by direction",
    ["kind", "model", "direction"],
)

LLM_COST_TOTAL = Counter(
    "astrobot_llm_cost_usd_total",
    "Approximate LLM cost in USD",
    ["kind", "model"],
)

ERRORS_TOTAL = Counter(
    "astrobot_errors_total",
    "Unhandled errors caught by global handler",
    ["error_type"],
)

DUPLICATE_UPDATES_TOTAL = Counter(
    "astrobot_duplicate_updates_total",
    "Telegram updates dropped as duplicates by idempotency layer",
)

FLOOD_RETRIES_TOTAL = Counter(
    "astrobot_flood_retries_total",
    "TelegramRetryAfter occurrences caught and retried",
)

CRISIS_TRIGGERED = Counter(
    "astrobot_crisis_triggered_total",
    "Crisis-keyword detector matched user input (LLM call skipped)",
)

PUSH_SENT = Counter(
    "astrobot_push_sent_total",
    "Push notifications dispatched",
    ["kind", "result"],
)

REFERRALS_REGISTERED = Counter(
    "astrobot_referrals_registered_total",
    "Successful referral applications",
)

FAVORITES_SAVED = Counter(
    "astrobot_favorites_saved_total",
    "Items added to favorites",
)

PAYMENTS_CREATED = Counter(
    "astrobot_payments_created_total",
    "Payment links created (pending)",
    ["item"],
)

PAYMENTS_SUCCEEDED = Counter(
    "astrobot_payments_succeeded_total",
    "Payments confirmed and granted",
    ["item"],
)

PAYMENTS_FAILED = Counter(
    "astrobot_payments_failed_total",
    "Payment creation or webhook processing failures",
    ["stage"],
)
