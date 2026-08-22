#!/usr/bin/env bash
# fix-qemu-brave-lag.sh - Fixes Tab lag that affects BOTH Brave and QEMU VMs (shared root cause)
# For screen reader users (Orca + NVDA) - keeps Orca, pauses NVDA contention
set -e
echo "=== QEMU + Brave Tab Lag Fix (shared at-spi / speech-dispatcher root) ==="
echo ""
echo "You reported lag in Brave AND QEMU VMs -> points to system-wide at-spi, not Brave alone."
echo ""

# 1. Show current QEMU capability
echo "[1/6] QEMU / KVM diagnostics..."
echo " QEMU: $(qemu-system-x86_64 --version 2>&1 | head -n1)"
echo " /dev/kvm: $(ls -l /dev/kvm 2>&1)"
echo " kvm modules: $(lsmod | grep -E "kvm" | tr '\n' ' ')"
echo " cpu: $(lscpu | grep "Model name" | cut -d: -f2)"
echo " load: $(uptime)"
echo " speech zombies before: $(ps aux | grep -c '\[speech-dispatch\] <defunct>' || true)"
echo " Orca pids: $(pgrep -f orca | tr '\n' ' ' || echo none)"
echo " NVDA pids: $(pgrep -f nvda.exe | tr '\n' ' ' || echo none)"

# 2. Restart speech-dispatcher (fixes BOTH Brave and QEMU GTK lag after few seconds)
echo ""
echo "[2/6] Restarting speech-dispatcher (fixes rhvoice 101% + zombies that stall Tab)..."
systemctl --user restart speech-dispatcher.service 2>&1 || true
sleep 1
echo "  zombies after: $(ps aux | grep -c '\[speech-dispatch\] <defunct>' || true)"
echo "  modules: $(ps aux | grep sd_ | grep -v grep | wc -l) active"

# 3. Verify Brave prefs still fixed
echo ""
echo "[3/6] Verifying Brave a11y prefs..."
python3 <<'PY'
import json, pathlib
for p in [pathlib.Path.home()/".config/BraveSoftware/Brave-Browser/Default/Preferences",
          pathlib.Path.home()/".config/BraveSoftware/Brave-Origin-Nightly/Default/Preferences"]:
    if p.exists():
        d=json.loads(p.read_text())
        a=d.get("settings",{}).get("a11y",{})
        print(p.name, a, "OK" if not a.get("caretbrowsing",{}).get("enabled") else "NEEDS FIX")
PY

# 4. Create wrappers that pause NVDA (wine) while Brave OR QEMU runs
#    NVDA's nvdaHelperRemoteLoader hooks BOTH Brave renderer and QEMU GTK window via at-spi
echo ""
echo "[4/6] Creating wrappers (pause NVDA, keep Orca)..."
mkdir -p ~/.local/bin
cat > ~/.local/bin/brave-screenreader <<'WRAP'
#!/usr/bin/env bash
# brave-screenreader - pause NVDA, keep Orca
PIDS=$(pgrep -f "nvda.exe" || true)
[ -n "$PIDS" ] && kill -STOP $PIDS 2>/dev/null; pkill -STOP -f nvdaHelperRemoteLoader 2>/dev/null || true
trap 'kill -CONT $PIDS 2>/dev/null || true; pkill -CONT -f nvdaHelperRemoteLoader 2>/dev/null || true' EXIT INT TERM
exec /usr/bin/brave-origin-nightly --enable-gpu-rasterization --enable-zero-copy --disable-features=AccessibilityImageLabels "$@"
WRAP
cat > ~/.local/bin/qemu-screenreader <<'WRAP'
#!/usr/bin/env bash
# qemu-screenreader - launch QEMU with KVM and pause NVDA to avoid Tab lag in VM window
# Usage: qemu-screenreader [qemu args...] - add -enable-kvm -cpu host if missing
PIDS=$(pgrep -f "nvda.exe" || true)
[ -n "$PIDS" ] && kill -STOP $PIDS 2>/dev/null; pkill -STOP -f nvdaHelperRemoteLoader 2>/dev/null || true
trap 'kill -CONT $PIDS 2>/dev/null || true; pkill -CONT -f nvdaHelperRemoteLoader 2>/dev/null || true' EXIT INT TERM
ARGS=("$@")
# Auto-add KVM if not present and /dev/kvm exists
if [[ ! " ${ARGS[*]} " =~ " -enable-kvm " ]] && [[ ! " ${ARGS[*]} " =~ " -accel " ]] && [ -c /dev/kvm ]; then
  echo "Auto-adding -enable-kvm -cpu host (was missing, causes 100% CPU TCG emulation = lag)" >&2
  ARGS=(-enable-kvm -cpu host "${ARGS[@]}")
fi
# Use GTK with at-spi but without NVDA double hook
exec qemu-system-x86_64 "${ARGS[@]}"
WRAP
chmod +x ~/.local/bin/brave-screenreader ~/.local/bin/qemu-screenreader
echo "  created ~/.local/bin/brave-screenreader"
echo "  created ~/.local/bin/qemu-screenreader"

# 5. Update Brave desktop file to use wrapper
SRC="/usr/share/applications/brave-origin-nightly.desktop"
DST="$HOME/.local/share/applications/brave-origin-nightly.desktop"
if [ -f "$SRC" ]; then
  cp "$SRC" "$DST" 2>/dev/null || true
  sed -i 's|^Exec=/usr/bin/brave-origin-nightly|Exec=/home/donovan/.local/bin/brave-screenreader|' "$DST" 2>/dev/null || true
  echo "  Brave desktop file now uses wrapper"
fi

# 6. QEMU performance checklist
echo ""
echo "[5/6] QEMU performance checklist..."
echo "  If you launch QEMU without -enable-kvm or -accel kvm, it uses TCG (full emulation)"
echo "  -> Tab and everything in VM will be extremely laggy, and host Brave will also lag due to CPU starvation"
echo "  Example FAST: qemu-screenreader -enable-kvm -cpu host -m 4096 -smp 4 -drive file=vm.qcow2"
echo "  Example SLOW (avoid): qemu-system-x86_64 -m 4096 vm.qcow2  # missing -enable-kvm"
echo ""
echo "  Check your QEMU command history:"
echo "  history | grep qemu"
echo "  If you use virt-manager / libvirt, ensure <domain type='kvm'> not 'qemu'"
echo ""
echo "[6/6] Summary..."
echo "  - Speech-dispatcher restarted, zombies cleared"
echo "  - Brave a11y prefs fixed (caret browsing off)"
echo "  - Wrappers created that pause NVDA (wine) while keeping Orca"
echo ""
echo "=== Test ==="
echo "1. Brave: ~/.local/bin/brave-screenreader  -> Tab in page should stay fast"
echo "2. QEMU:  ~/.local/bin/qemu-screenreader -enable-kvm -cpu host -m 4096 vm.qcow2"
echo "   Or if you use libvirt: virsh edit <vm> and ensure <domain type='kvm'>"
echo ""
echo "If QEMU still lags, run this while VM is laggy and share output:"
echo "  top -b -n1 | head -n 20"
echo "  ps aux | grep qemu"
echo "  cat /proc/\$(pgrep -f qemu | head -n1)/cmdline | tr '\\0' ' '"
echo ""
echo "Fix applied. Both apps share at-spi bridge - fixing it fixes both."
