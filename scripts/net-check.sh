#!/usr/bin/env bash
# Is the network path to the MAX API the reason the bot feels slow?
#
# Run ON THE SERVER. Hits the MAX API and (as a control on the same box, same
# moment) the Telegram API, N times, and reports the connection breakdown.
# Neither request is authenticated — a 401 is expected and fine; we're timing
# the network path, not the API.
#
#   ./scripts/net-check.sh [iterations]
#
# Reading the result:
#   - handshake (connect+tls) steady and <0.3s  → the route is healthy, the
#     latency is in our code or in MAX's own delivery queue.
#   - handshake jumps around, occasional multi-second samples, or failures
#     → packet loss / throttling on the way to Russian infra. No amount of
#     application tuning fixes that; a Russian VPS would.
#   - MAX slow while Telegram on the same line stays fast → it's the route to
#     MAX specifically, not the server's uplink.
set -euo pipefail

N="${1:-20}"
FMT='%{http_code} dns=%{time_namelookup} conn=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n'

# Probe from inside app-max: the Минцифры root that signs platform-api2.max.ru
# is installed in the IMAGE (see Dockerfile), not on the host, and this is the
# exact network path the bot uses. Falls back to the host with verification off
# (-k still performs the full handshake, so the timings stay comparable).
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.prod.yml}"
if [[ -n "$(docker compose ps -q app-max 2>/dev/null)" ]]; then
  CURL=(docker compose exec -T app-max curl)
  echo "probing from inside the app-max container"
else
  CURL=(curl -k)
  echo "app-max container not found — probing from the host, TLS verification off"
fi
echo

probe() {
  local label="$1" url="$2"
  echo "── $label ($url)"
  local totals=() fails=0 i out
  for ((i = 1; i <= N; i++)); do
    if out=$("${CURL[@]}" -sS -o /dev/null --max-time 20 -w "$FMT" "$url" 2>&1); then
      echo "  $out"
      totals+=("${out##*total=}")
    else
      echo "  FAILED: $out"
      fails=$((fails + 1))
    fi
  done
  if ((${#totals[@]})); then
    printf '%s\n' "${totals[@]}" | sort -n | awk -v f="$fails" '
      {v[NR] = $1; s += $1}
      END {
        printf "  → min %.2fs  median %.2fs  max %.2fs  avg %.2fs  failed %d/%d\n",
               v[1], v[int((NR + 1) / 2)], v[NR], s / NR, f, NR + f
      }'
  else
    echo "  → all $fails requests failed"
  fi
  echo
}

probe "MAX" "https://platform-api2.max.ru/me"
probe "Telegram (control)" "https://api.telegram.org/bot0:0/getMe"

# Loss and per-hop latency tell throttling apart from a plain long route.
if command -v mtr >/dev/null 2>&1; then
  echo "── mtr to platform-api2.max.ru (loss% per hop)"
  mtr -r -c 20 platform-api2.max.ru
else
  echo "mtr not installed — 'apt install mtr-tiny' for per-hop loss"
fi
