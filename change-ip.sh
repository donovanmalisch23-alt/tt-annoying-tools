#!/usr/bin/env bash
# change-ip.sh — force a new DHCP lease (and therefore a new IP) on an
# interface managed by NetworkManager.
#
# Changing the MAC address alone does not change the IP: NetworkManager
# keeps the existing DHCP lease while the connection stays active, and its
# cached lease files can make it reuse the old client identifier even after
# the MAC changes.  This script does the full sequence properly:
#
#   1. deactivate the connection (releases the lease)
#   2. randomize the MAC via the connection's cloned-mac-address
#   3. delete NetworkManager's cached lease files for the connection
#   4. reactivate the connection so a fresh DHCP DISCOVER goes out
#   5. verify the IP changed, retrying with a new MAC if it did not
#
# Note: this changes the machine's LAN IP.  The public IP seen by remote
# servers is assigned by your ISP to the router and is not affected.
#
# Usage:
#   sudo ./change-ip.sh [--interface IFACE] [--attempts N]
#                       [--randomize-hostname] [--force] [--dry-run]
#   sudo ./change-ip.sh --restore
set -euo pipefail

INTERFACE=""
ATTEMPTS=3
RANDOMIZE_HOSTNAME=0
FORCE=0
DRY_RUN=0
RESTORE=0
STATE_FILE="$(dirname "$(readlink -f "$0")")/.change-ip-state"
# Keep the original arguments: the parsing loop below shifts them away, but
# the sudo re-exec must pass them on unchanged.
ORIG_ARGS=("$@")

usage() {
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

log()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --interface)          INTERFACE="$2"; shift 2 ;;
        --attempts)           ATTEMPTS="$2"; shift 2 ;;
        --randomize-hostname) RANDOMIZE_HOSTNAME=1; shift ;;
        --force)              FORCE=1; shift ;;
        --dry-run)            DRY_RUN=1; shift ;;
        --restore)            RESTORE=1; shift ;;
        -h|--help)            usage 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

case "$ATTEMPTS" in
    ''|*[!0-9]*) die "--attempts must be a positive integer" ;;
esac

[ "$(id -u)" -eq 0 ] || exec sudo "$0" "${ORIG_ARGS[@]}"

command -v nmcli >/dev/null 2>&1 || die "nmcli not found; this script needs NetworkManager"

# --- discover the interface and its active connection ----------------------
if [ -z "$INTERFACE" ]; then
    INTERFACE="$(nmcli -t -f DEVICE,TYPE connection show --active \
        | awk -F: '$2 ~ /ethernet/ { print $1; exit }')"
    [ -n "$INTERFACE" ] || die "no active ethernet connection found; pass --interface"
fi
CONN="$(nmcli -t -f NAME,DEVICE connection show --active \
    | awk -F: -v iface="$INTERFACE" '$2 == iface { print $1; exit }')"
[ -n "$CONN" ] || die "interface $INTERFACE has no active NetworkManager connection"

current_ip() {
    nmcli -t -f IP4.ADDRESS device show "$INTERFACE" 2>/dev/null \
        | awk -F'[:/]' '$1 ~ /^IP4.ADDRESS/ { print $2; exit }'
}
current_mac() {
    ip link show "$INTERFACE" 2>/dev/null \
        | awk '/link\/ether/ { print $2; exit }'
}

OLD_IP="$(current_ip)"
OLD_MAC="$(current_mac)"
[ -n "$OLD_IP" ] || die "could not read the current IP of $INTERFACE"
log "Interface:   $INTERFACE"
log "Connection:  $CONN"
log "Current IP:  $OLD_IP"
log "Current MAC: $OLD_MAC"

# --- restore mode: put the original MAC/hostname back ----------------------
if [ "$RESTORE" -eq 1 ]; then
    if [ -f "$STATE_FILE" ]; then
        # shellcheck disable=SC1090
        . "$STATE_FILE"
        log "Restoring saved settings (MAC ${SAVED_MAC:-permanent}, hostname '${SAVED_HOSTNAME:-default}')."
        nmcli connection modify "$CONN" \
            802-3-ethernet.cloned-mac-address "${SAVED_MAC:-}" \
            ipv4.dhcp-hostname "${SAVED_HOSTNAME:-}"
        rm -f "$STATE_FILE"
    else
        log "No saved state; resetting to the permanent MAC and default hostname."
        nmcli connection modify "$CONN" \
            802-3-ethernet.cloned-mac-address "" \
            ipv4.dhcp-hostname ""
    fi
    nmcli connection down "$CONN" 2>/dev/null || true
    nmcli connection up "$CONN"
    log "Restored. IP is now $(current_ip)."
    exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
    log "Dry run: would deactivate $CONN, randomize the MAC, clear cached"
    log "leases, and reactivate to request a new DHCP lease."
    exit 0
fi

if [ "$FORCE" -eq 0 ]; then
    printf 'This will briefly disconnect %s (%s) to request a new IP.\n' \
        "$INTERFACE" "$CONN"
    printf 'Continue? [y/N] '
    read -r answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *) log "Aborted."; exit 0 ;;
    esac
fi

# --- save the original profile values so --restore can put them back -------
ORIG_MAC="$(nmcli -t -f 802-3-ethernet.cloned-mac-address connection show "$CONN" \
    | cut -d: -f2-)"
ORIG_HOSTNAME="$(nmcli -t -f ipv4.dhcp-hostname connection show "$CONN" \
    | cut -d: -f2-)"
printf 'SAVED_MAC=%q\nSAVED_HOSTNAME=%q\n' "${ORIG_MAC:-}" "${ORIG_HOSTNAME:-}" \
    > "$STATE_FILE"

# --- the main loop: down, new MAC, clear leases, up, verify ----------------
for attempt in $(seq 1 "$ATTEMPTS"); do
    log "Attempt $attempt/$ATTEMPTS: requesting a new DHCP lease…"

    nmcli connection down "$CONN" 2>/dev/null || true

    # A fresh random MAC on every activation; NM applies it when the
    # connection comes up, so it cannot be reverted by the manager.
    nmcli connection modify "$CONN" 802-3-ethernet.cloned-mac-address random

    if [ "$RANDOMIZE_HOSTNAME" -eq 1 ]; then
        # No pipes here: head closing early would SIGPIPE tr, and pipefail
        # would abort the script.
        new_host="pc-$(printf '%08x' "$((RANDOM * 32768 + RANDOM))")"
        nmcli connection modify "$CONN" ipv4.dhcp-hostname "$new_host"
        log "  DHCP hostname randomized to $new_host."
    fi

    # Drop cached lease files so the old client identifier cannot be reused.
    for lease in /var/lib/NetworkManager/*.lease; do
        [ -e "$lease" ] || continue
        case "$lease" in
            *"$INTERFACE"*|*"$CONN"*) rm -f "$lease" ;;
        esac
    done

    nmcli connection up "$CONN"

    # Wait for the interface to come back with an address.
    new_ip=""
    for _ in $(seq 1 30); do
        new_ip="$(current_ip)"
        [ -n "$new_ip" ] && break
        sleep 1
    done
    [ -n "$new_ip" ] || die "no IP after reactivating $INTERFACE"

    new_mac="$(current_mac)"
    log "  New MAC: $new_mac"
    log "  New IP:  $new_ip"

    if [ "$new_ip" != "$OLD_IP" ]; then
        log "Success: IP changed from $OLD_IP to $new_ip."
        exit 0
    fi
    log "  IP unchanged; the DHCP server returned the same address."
done

warn "IP did not change after $ATTEMPTS attempt(s)."
warn "The router may assign addresses by hostname or have a static lease."
warn "Try: sudo $0 --interface $INTERFACE --randomize-hostname"
warn "or check the router's DHCP configuration for a reservation."
exit 1
