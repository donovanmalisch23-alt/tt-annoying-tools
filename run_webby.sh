#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_webby.sh — start and manage the Webby control panel.
#
# Webby is the local bridge that lets the browser panel drive the *real*
# TeamTalk tools (a page cannot open a TCP connection to port 10333 itself) and
# that owns the allowlist file the admin panel edits.
#
# `bun run dev` starts the bridge for you and proxies /api to it, so the dev
# server and the preview are reachable without this script. Use this script when
# you want the panel served by the bridge itself (one origin, no dev server),
# when you want a specific allowlist file, or to manage it as a service.
#
#   ./run_webby.sh start                 build if needed, then serve in the background
#   ./run_webby.sh stop                  stop the bridge
#   ./run_webby.sh restart               stop then start
#   ./run_webby.sh status                is it up, and what is it serving
#   ./run_webby.sh logs [-f]             show (or follow) the bridge log
#   ./run_webby.sh build                 build the panel into dist/
#   ./run_webby.sh admin set             create/replace the admin credential
#   ./run_webby.sh admin show            who the admin is (never the password)
#   ./run_webby.sh doctor                check python, node, the SDK, the allowlist
#   ./run_webby.sh selftest              run the bridge's own end-to-end checks
#   ./run_webby.sh open                  open the panel in a browser
#
# Everything is overridable by flag or environment variable:
#
#   --port 8787            WEBBY_PORT               bridge port
#   --host 0.0.0.0         WEBBY_HOST               bridge bind address
#   --whitelist PATH       WEBBY_WHITELIST          ANY allowlist file to edit
#   --admin-file PATH      WEBBY_ADMIN_FILE         where the admin hash lives
#   --state-dir PATH       WEBBY_STATE_DIR          pid + log directory
#   --accept-sdk-license   WEBBY_ACCEPT_SDK_LICENSE pass --accept-sdk-license on
#   --require-admin        WEBBY_REQUIRE_ADMIN      require sign-in to start runs
#   WEBBY_PYTHON                                     python interpreter to use
#   WEBBY_PM                                         package manager (bun or npm)
#   WEBBY_SKIP_BUILD=1                               never build the panel
#   WEBBY_ADMIN_USER / WEBBY_ADMIN_PASSWORD          skip the generated first-run login
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="${WEBBY_PYTHON:-}"
PORT="${WEBBY_PORT:-8787}"
HOST="${WEBBY_HOST:-0.0.0.0}"
WHITELIST="${WEBBY_WHITELIST:-}"
ADMIN_FILE="${WEBBY_ADMIN_FILE:-}"
STATE_DIR="${WEBBY_STATE_DIR:-$ROOT/.webby}"
ACCEPT_LICENSE="${WEBBY_ACCEPT_SDK_LICENSE:-}"
REQUIRE_ADMIN="${WEBBY_REQUIRE_ADMIN:-}"
LOG_FILE="$STATE_DIR/webby.log"
LAUNCH_PID_FILE="$STATE_DIR/webby.launch.pid"

COMMAND="${1:-help}"
[ $# -gt 0 ] && shift || true

# --- option parsing -------------------------------------------------------- #
EXTRA_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --host) HOST="${2:?--host needs a value}"; shift 2 ;;
    --whitelist) WHITELIST="${2:?--whitelist needs a value}"; shift 2 ;;
    --admin-file) ADMIN_FILE="${2:?--admin-file needs a value}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:?--state-dir needs a value}"; shift 2 ;;
    --accept-sdk-license) ACCEPT_LICENSE=1; shift ;;
    --require-admin) REQUIRE_ADMIN=1; shift ;;
    -f|--follow) EXTRA_ARGS+=("-f"); shift ;;
    --username) EXTRA_ARGS+=("--username" "${2:?--username needs a value}"); shift 2 ;;
    --password) EXTRA_ARGS+=("--password" "${2:?--password needs a value}"); shift 2 ;;
    --generate) EXTRA_ARGS+=("--generate"); shift ;;
    --force) EXTRA_ARGS+=("--force"); shift ;;
    --help|-h) COMMAND="help"; shift ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

LOG_FILE="$STATE_DIR/webby.log"
LAUNCH_PID_FILE="$STATE_DIR/webby.launch.pid"

# --- helpers --------------------------------------------------------------- #

die() { printf 'run_webby.sh: %s\n' "$1" >&2; exit 1; }
note() { printf '==> %s\n' "$1"; }
warn() { printf 'warning: %s\n' "$1" >&2; }

find_python() {
  if [ -n "$PY" ]; then
    command -v "$PY" >/dev/null 2>&1 || die "WEBBY_PYTHON=$PY is not executable"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then PY="python3"
  elif command -v python >/dev/null 2>&1; then PY="python"
  else die "python 3 is required but was not found on PATH"; fi
}

find_pm() {
  if [ -n "${WEBBY_PM:-}" ]; then echo "$WEBBY_PM"; return; fi
  if command -v bun >/dev/null 2>&1; then echo "bun"
  elif command -v npm >/dev/null 2>&1; then echo "npm"
  else echo ""; fi
}

bridge_args() { # prints the flag array, one argument per line
  printf '%s\n' --repo-root "$ROOT" --host "$HOST" --port "$PORT" --state-dir "$STATE_DIR"
  if [ -n "$WHITELIST" ]; then printf '%s\n' --whitelist "$WHITELIST"; fi
  if [ -n "$ADMIN_FILE" ]; then printf '%s\n' --admin-file "$ADMIN_FILE"; fi
  if [ -n "$ACCEPT_LICENSE" ]; then printf '%s\n' --accept-sdk-license; fi
  if [ -n "$REQUIRE_ADMIN" ]; then printf '%s\n' --require-admin; fi
}

run_bridge() { # run the bridge CLI with the configured flags
  local argv=()
  while IFS= read -r line; do argv+=("$line"); done < <(bridge_args)
  "$PY" -u -m webby "$@" "${argv[@]}"
}

url() {
  case "$HOST" in
    0.0.0.0|::|"") echo "http://127.0.0.1:$PORT/" ;;
    *) echo "http://$HOST:$PORT/" ;;
  esac
}

panel_built() { [ -f "$ROOT/dist/index.html" ]; }

build_panel() {
  local pm
  pm="$(find_pm)"
  if [ -z "$pm" ]; then
    warn "neither bun nor npm is on PATH; cannot build the panel"
    return 1
  fi
  note "building the panel with $pm"
  if [ ! -d "$ROOT/node_modules" ]; then
    ( cd "$ROOT" && "$pm" install )
  fi
  ( cd "$ROOT" && "$pm" run build )
  panel_built || die "the build did not produce dist/index.html"
}

ensure_panel() {
  if panel_built; then return 0; fi
  if [ "${WEBBY_SKIP_BUILD:-0}" = "1" ]; then
    warn "dist/ is missing and WEBBY_SKIP_BUILD=1; the bridge will serve a 'build me' page"
    return 0
  fi
  build_panel || true
}

await_health() {
  local tries="${1:-40}" i
  for i in $(seq 1 "$tries"); do
    if run_bridge status >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  return 1
}

open_browser() {
  local target="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    (xdg-open "$target" >/dev/null 2>&1 &)
  elif command -v open >/dev/null 2>&1; then
    (open "$target" >/dev/null 2>&1 &)
  else
    warn "no browser opener found; open $target yourself"
  fi
}

# --- commands -------------------------------------------------------------- #

ensure_admin() {
  local admin_file="${ADMIN_FILE:-$STATE_DIR/admin.json}"
  if [ -f "$admin_file" ]; then return 0; fi
  if [ -n "${WEBBY_ADMIN_PASSWORD:-}" ]; then
    note "creating the admin credential from WEBBY_ADMIN_PASSWORD"
    run_bridge admin set --username "${WEBBY_ADMIN_USER:-admin}" --password "$WEBBY_ADMIN_PASSWORD" >/dev/null
    return 0
  fi
  note "no admin credential yet — generating one (printed once, below)"
  run_bridge admin set --username "${WEBBY_ADMIN_USER:-admin}" --generate || true
  echo "   (change it later with: ./run_webby.sh admin set --username admin)"
}

cmd_start() {
  find_python
  mkdir -p "$STATE_DIR"
  ensure_admin
  ensure_panel

  if run_bridge status >/dev/null 2>&1; then
    note "already running at $(url) (use './run_webby.sh restart' to relaunch)"
    exit 0
  fi

  local argv=()
  while IFS= read -r line; do argv+=("$line"); done < <(bridge_args)

  note "starting the bridge on $HOST:$PORT"
  nohup "$PY" -u -m webby serve "${argv[@]}" >>"$LOG_FILE" 2>&1 &
  echo $! >"$LAUNCH_PID_FILE"

  if await_health 40; then
    note "up at $(url)"
    echo "   panel     $(url)"
    echo "   logs      ./run_webby.sh logs -f"
    echo "   allowlist ${WHITELIST:-$ROOT/whitelist.txt}"
    echo "   admin     ./run_webby.sh admin set --username admin"
    echo "   stop      ./run_webby.sh stop"
    if [ -z "$(find_pm)" ] && ! panel_built; then
      warn "the panel is not built and no package manager was found; only the API is live"
    fi
    exit 0
  fi

  warn "the bridge did not answer /api/health in time"
  echo "--- last 25 log lines ($LOG_FILE) ---" >&2
  tail -n 25 "$LOG_FILE" >&2 || true
  exit 1
}

cmd_stop() {
  find_python
  if run_bridge status >/dev/null 2>&1; then
    run_bridge stop
    exit 0
  fi
  # The bridge never got as far as writing its pid file, but the launcher may
  # still hold the process.
  if [ -f "$LAUNCH_PID_FILE" ]; then
    local pid
    pid="$(cat "$LAUNCH_PID_FILE" 2>/dev/null || echo "")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      note "stopped the half-started bridge (pid $pid)"
      rm -f "$LAUNCH_PID_FILE"
      exit 0
    fi
    rm -f "$LAUNCH_PID_FILE"
  fi
  run_bridge stop
}

cmd_restart() {
  find_python
  run_bridge stop >/dev/null 2>&1 || true
  sleep 0.3
  cmd_start
}

cmd_status() {
  find_python
  run_bridge status
}

cmd_logs() {
  if [ ! -f "$LOG_FILE" ]; then
    die "no log yet at $LOG_FILE (start the bridge first)"
  fi
  if [ "${#EXTRA_ARGS[@]}" -gt 0 ]; then
    tail "${EXTRA_ARGS[@]}" "$LOG_FILE"
  else
    tail -n 100 "$LOG_FILE"
  fi
}

cmd_build() {
  build_panel
  note "panel written to $ROOT/dist"
}

cmd_admin() {
  find_python
  if [ "${#EXTRA_ARGS[@]}" -eq 0 ]; then EXTRA_ARGS=(show); fi
  run_bridge admin "${EXTRA_ARGS[@]}"
}

cmd_selftest() {
  find_python
  note "running the bridge self test (no server, no SDK needed)"
  "$PY" -u -m webby.selftest
}

cmd_open() {
  local target
  target="$(url)"
  note "opening $target"
  open_browser "$target"
}

cmd_doctor() {
  local problems=0 pm admin wl
  printf 'Webby doctor\n------------\n'
  printf 'repo root      %s\n' "$ROOT"

  if find_python 2>/dev/null; then :; fi
  if [ -n "$PY" ]; then
    printf 'python         %s (%s)\n' "$PY" "$("$PY" --version 2>&1)"
  else
    printf 'python         MISSING\n'; problems=$((problems + 1))
  fi

  pm="$(find_pm)"
  if [ -n "$pm" ]; then
    printf 'panel tooling  %s (%s)\n' "$pm" "$("$pm" --version 2>/dev/null | head -n1)"
  else
    printf 'panel tooling  none (bun/npm not found)\n'; problems=$((problems + 1))
  fi

  if panel_built; then
    printf 'panel build    dist/index.html present\n'
  else
    printf 'panel build    MISSING (run: ./run_webby.sh build)\n'; problems=$((problems + 1))
  fi

  if [ -f "$ROOT/sdk/TeamTalk5.py" ]; then
    printf 'SDK python     sdk/TeamTalk5.py present\n'
  else
    printf 'SDK python     MISSING in sdk/\n'; problems=$((problems + 1))
  fi
  if ls "$ROOT"/sdk/libTeamTalk5.* >/dev/null 2>&1; then
    printf 'SDK library    %s\n' "$(ls "$ROOT"/sdk/libTeamTalk5.* | xargs -n1 basename | tr '\n' ' ')"
  else
    printf 'SDK library    MISSING in sdk/\n'; problems=$((problems + 1))
  fi

  wl="${WHITELIST:-$ROOT/whitelist.txt}"
  if [ -f "$wl" ]; then
    printf 'allowlist      %s (%s entr(ies))\n' "$wl" "$(grep -cvE '^[[:space:]]*(#|$)' "$wl" || true)"
  else
    printf 'allowlist      %s does not exist yet\n' "$wl"
  fi

  admin="${ADMIN_FILE:-$STATE_DIR/admin.json}"
  if [ -f "$admin" ]; then
    printf 'admin          configured (%s)\n' "$admin"
  else
    printf 'admin          not configured (run: ./run_webby.sh admin set --username admin)\n'
  fi

  printf 'state dir      %s\n' "$STATE_DIR"
  if run_bridge status >/dev/null 2>&1; then
    printf 'bridge         running at %s\n' "$(url)"
  else
    printf 'bridge         not running\n'
  fi

  printf -- '------------\n'
  if [ "$problems" -eq 0 ]; then
    printf 'no problems found\n'
  else
    printf '%d item(s) need attention\n' "$problems"
  fi
  [ "$problems" -eq 0 ]
}

cmd_help() {
  # Print the header comment: everything between the two "# ---" rules.
  awk 'NR==1 {next} /^# ---/ {n++; if (n==2) exit; next} {sub(/^# ?/, ""); print}' "$0"
}

case "$COMMAND" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart|reload) cmd_restart ;;
  status) cmd_status ;;
  logs|log) cmd_logs ;;
  build) cmd_build ;;
  admin) cmd_admin ;;
  open) cmd_open ;;
  doctor|check) cmd_doctor ;;
  selftest|test) cmd_selftest ;;
  help|-h|--help) cmd_help ;;
  *) die "unknown command '$COMMAND' (try: ./run_webby.sh help)" ;;
esac
