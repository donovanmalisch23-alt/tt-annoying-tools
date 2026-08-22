#!/usr/bin/env bash
# change-public-ip.sh — change the public IP address that remote servers see.
#
# The public IP is assigned by your ISP to your router's WAN interface, not
# to this machine.  No local change (MAC address, LAN IP, DHCP lease) affects
# it.  The reliable way to get a new public IP is to change the router's WAN
# MAC address and reboot it, which makes the ISP's DHCP server hand out a new
# address.  This script shows your current public IP, prints the router steps,
# and (with --watch) confirms when the address actually changes.
#
# Usage:
#   ./change-public-ip.sh                 # show current public IP + steps
#   ./change-public-ip.sh --watch         # poll until the public IP changes
#   ./change-public-ip.sh --watch --interval 5 --timeout 600
set -euo pipefail

WATCH=0
INTERVAL=10
TIMEOUT=300

usage() {
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

log()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --watch)    WATCH=1; shift ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        --timeout)  TIMEOUT="$2"; shift 2 ;;
        -h|--help)  usage 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

case "$INTERVAL" in ''|*[!0-9]*) die "--interval must be a positive integer" ;; esac
case "$TIMEOUT"  in ''|*[!0-9]*) die "--timeout must be a positive integer" ;; esac

# --- public IP detection (try several services, take the first that works) --
public_ip() {
    for url in \
        "https://api.ipify.org" \
        "https://ifconfig.me/ip" \
        "https://icanhazip.com" \
        "https://ipinfo.io/ip"; do
        ip="$(curl -s --max-time 8 "$url" 2>/dev/null | tr -d '[:space:]')"
        case "$ip" in
            ''|*[!0-9.]*) continue ;;
        esac
        printf '%s' "$ip"
        return 0
    done
    return 1
}

# --- router detection -------------------------------------------------------
GATEWAY="$(ip route | awk '/default/ { print $3; exit }')"
[ -n "$GATEWAY" ] || die "could not determine the default gateway"

router_kind() {
    # A tiny fingerprint: TP-Link's web UI redirects to /webpages/login.html.
    if curl -s --max-time 5 "http://$GATEWAY/" 2>/dev/null | grep -q "webpages/login.html"; then
        echo "tp-link"
    else
        echo "unknown"
    fi
}

OLD_IP="$(public_ip)" || die "could not determine the current public IP"
log "Current public IP: $OLD_IP"
log "Router gateway:    $GATEWAY"

KIND="$(router_kind)"
log "Router type:       $KIND"

# --- the steps --------------------------------------------------------------
log ""
log "To change the public IP, do the following on the router:"
log ""
if [ "$KIND" = "tp-link" ]; then
    log "  1. Open http://$GATEWAY in a browser and log in (admin password)."
    log "  2. Go to Advanced -> Network -> Internet."
    log "  3. Find 'MAC Clone' (or 'MAC Address' under Internet settings)."
    log "  4. Set it to a new random MAC (change a few digits)."
    log "  5. Save, then reboot the router (System -> Reboot, or power-cycle)."
else
    log "  1. Log in to the router's admin page at http://$GATEWAY."
    log "  2. Find the WAN / Internet settings and the 'MAC Clone' option."
    log "  3. Set a new random WAN MAC address and save."
    log "  4. Reboot the router (or power-cycle it)."
fi
log ""
log "If the IP does not change after a reboot:"
log "  - Your ISP may use PPPoE: a plain reboot (no MAC change) often suffices."
log "  - Your ISP may assign a sticky/static IP: only the ISP can change it."
log "  - A VPN/proxy changes what servers see, but to a shared IP, not yours."

if [ "$WATCH" -eq 0 ]; then
    log ""
    log "Run '$0 --watch' after the router reboots to confirm the change."
    exit 0
fi

# --- watch mode -------------------------------------------------------------
log ""
log "Watching for the public IP to change (every ${INTERVAL}s, up to ${TIMEOUT}s)…"
log "Go change the router's WAN MAC and reboot it now."
deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    sleep "$INTERVAL"
    new_ip="$(public_ip)" || { warn "public IP lookup failed; retrying"; continue; }
    if [ "$new_ip" != "$OLD_IP" ]; then
        log ""
        log "Public IP changed: $OLD_IP -> $new_ip"
        exit 0
    fi
    log "  still $new_ip …"
done
warn "Public IP did not change within ${TIMEOUT}s."
warn "See the notes above (PPPoE, sticky IP, or ISP-assigned static address)."
exit 1
