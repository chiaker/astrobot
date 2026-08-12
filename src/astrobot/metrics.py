from __future__ import annotations

import os

from prometheus_client import Counter, Gauge, Histogram

# Which build produced the numbers on a graph. Every debugging session so far has
# stalled on "is the fix even deployed yet?" — this answers it in Grafana, next to
# the data, instead of over ssh.
BUILD_INFO = Gauge("astrobot_build_info", "Build metadata; the value is always 1", ["sha"])
BUILD_INFO.labels(sha=os.environ.get("ASTROBOT_GIT_SHA", "unknown")).set(1)

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

UPDATE_DURATION = Histogram(
    "astrobot_update_duration_seconds",
    "End-to-end handling time of one incoming update, by callback prefix / update type",
    ["kind"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)

UPDATE_PREP = Histogram(
    "astrobot_update_prep_seconds",
    "Between the update arriving and the handler starting: acquiring a pooled DB "
    "connection and the get-or-create user query",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)

DB_POOL_IN_USE = Gauge(
    "astrobot_db_pool_in_use",
    "DB connections checked out of the pool. Pinned at pool_size+max_overflow "
    "means every new update waits up to pool_timeout for a free one",
)

UPDATE_LAG = Histogram(
    "astrobot_update_lag_seconds",
    "Delay from the messenger-side event timestamp to the start of our handling "
    "(platform delivery queue + webhook + event-loop backlog). Subject to clock "
    "skew between the platform and this host; clamped at 0",
    ["kind"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)

CALLBACK_UNANSWERED = Counter(
    "astrobot_callback_unanswered_total",
    "Button presses the handler never answered (MAX rejects a contentless ack). "
    "Suspected of making MAX withhold the user's NEXT press until it times out",
)

MAX_API_DURATION = Histogram(
    "astrobot_max_api_duration_seconds",
    "One outgoing call to the MAX API, including maxapi's internal retries",
    ["op"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120),
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

PAYMENTS_REFUNDED = Counter(
    "astrobot_payments_refunded_total",
    "Payments refunded and benefits revoked",
    ["item"],
)
