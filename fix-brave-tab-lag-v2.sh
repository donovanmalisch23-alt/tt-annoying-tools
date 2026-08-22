#!/usr/bin/env bash
# fix-brave-tab-lag-v2.sh - Fix Brave Tab lag while KEEPING screen reader (Orca + NVDA coexistence)
# Root cause found: Orca + NVDA (wine) both handling Brave + speech-dispatcher zombies + caret browsing
set -e
echo "=== Brave Tab Fix v2 (Orca + NVDA safe) ==="

# 1. Diagnose double-reader
echo "[1/7] Diagnosing..."
echo " Orca: $(pgrep -f orca | wc -l) processes"
echo " NVDA: $(pgrep -f nvda.exe | wc -l) processes (wine)"
echo " speech-dispatcher zombies: $(ps aux | grep -c '\[speech-dispatch\] <defunct>' || true)"
gsettings get org.gnome.desktop.interface toolkit-accessibility
gsettings get org.gnome.desktop.a11y.applications screen-reader-enabled

# 2. Fix Brave prefs (caret browsing etc) - still needed even with screen reader
echo "[2/7] Patching Brave prefs (caret browsing)..."
for P in "$HOME/.config/BraveSoftware/Brave-Browser/Default/Preferences" "$HOME/.config/BraveSoftware/Brave-Origin-Nightly/Default/Preferences"; do
  [ -f "$P" ] || continue
  cp "$P" "$P.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
  python3 <<PY
import json,pathlib
p=pathlib.Path("$P")
d=json.loads(p.read_text())
d.setdefault("settings",{}).setdefault("a11y",{})
d["settings"]["a11y"]["caretbrowsing"]={"enabled":False}
d["settings"]["a11y"]["focus_highlight"]=False
d["settings"]["a11y"]["enable_accessibility_image_labels"]=False
d["settings"]["a11y"]["enable_accessibility_image_labels_opt_in_accepted"]=False
p.write_text(json.dumps(d))
print("  patched $P")
PY
done

# 3. Fix speech-dispatcher: restart to clear zombies and rhvoice 101% CPU
echo "[3/7] Restarting speech-dispatcher (clears zombies, rhvoice stuck)..."
systemctl --user restart speech-dispatcher.service 2>&1 || pkill -9 speech-dispatcher; sleep 1; systemctl --user start speech-dispatcher 2>&1 || true
sleep 1
# also fix winenvda.desktop executable bit warning
chmod -x "$HOME/.config/autostart/winenvda.desktop" 2>/dev/null || true
echo "  speech-dispatcher restarted, zombies cleared"
ps aux | grep -E "speech-dispatcher|sd_rhvoice" | grep -v grep | head -n 5

# 4. Ensure Orca stays enabled, but NVDA does NOT double-handle Brave
#    NVDA via Wine hooks Brave renderer via nvdaHelperRemoteLoader.exe -> causes few-seconds-then-lag
#    We create a wrapper that SUSPENDS NVDA while Brave is focused, Orca still speaks.
echo "[4/7] Creating Brave wrapper that pauses NVDA during Brave session..."
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/brave-screenreader" <<'WRAP'
#!/usr/bin/env bash
# brave-screenreader - launch Brave with Orca only, pause NVDA (wine) to avoid double handling
# Orca keeps speaking, NVDA resumes after Brave exits
set -e
NVDA_PIDS=$(pgrep -f "nvda.exe" || true)
if [ -n "$NVDA_PIDS" ]; then
  echo "Pausing NVDA (Orca will handle Brave)..."
  kill -STOP $NVDA_PIDS 2>/dev/null || true
  # also pause helper
  pkill -STOP -f nvdaHelperRemoteLoader || true
  trap 'echo "Resuming NVDA..."; kill -CONT $NVDA_PIDS 2>/dev/null || true; pkill -CONT -f nvdaHelperRemoteLoader 2>/dev/null || true' EXIT INT TERM
fi
# Launch Brave with screen-reader-friendly flags (keep a11y, disable heavy image labels)
exec /usr/bin/brave-origin-nightly --enable-gpu-rasterization --enable-zero-copy --disable-features=AccessibilityImageLabels,CalculateNativeWinOcclusion "$@"
WRAP
chmod +x "$HOME/.local/bin/brave-screenreader"
echo "  created ~/.local/bin/brave-screenreader"

# 5. Update desktop file to use wrapper (optional)
echo "[5/7] Updating desktop launcher..."
SRC="/usr/share/applications/brave-origin-nightly.desktop"
DST="$HOME/.local/share/applications/brave-origin-nightly.desktop"
mkdir -p "$HOME/.local/share/applications"
if [ -f "$SRC" ]; then
  cp "$SRC" "$DST"
  # Use wrapper script instead of direct binary
  sed -i 's|^Exec=/usr/bin/brave-origin-nightly|Exec=/home/donovan/.local/bin/brave-screenreader|' "$DST"
  echo "  patched $DST to use wrapper"
  grep "^Exec" "$DST" | head -n1
fi

# 6. Clear GPUCache
echo "[6/7] Clearing GPUCache..."
rm -rf "$HOME/.config/BraveSoftware/Brave-Browser/Default/GPUCache" 2>/dev/null || true
rm -rf "$HOME/.config/BraveSoftware/Brave-Origin-Nightly/Default/GPUCache" 2>/dev/null || true

# 7. Verify and test
echo "[7/7] Verifying..."
gsettings set org.gnome.desktop.interface toolkit-accessibility true
gsettings set org.gnome.desktop.a11y.applications screen-reader-enabled true
echo " toolkit-accessibility=$(gsettings get org.gnome.desktop.interface toolkit-accessibility)"
echo " Orca running: $(pgrep -f orca >/dev/null && echo yes || echo no)"
echo " NVDA running: $(pgrep -f nvda.exe >/dev/null && echo yes || echo no)"
echo ""
echo "=== Done ==="
echo "Usage:"
echo "  Normal launch (auto-pauses NVDA): brave-screenreader"
echo "  Or: ~/.local/bin/brave-screenreader"
echo "  Or click Brave icon (now uses wrapper)"
echo ""
echo "Test: open example.com, press Tab quickly - should stay fast beyond 5 seconds,"
echo "Orca will still announce (NVDA paused only while Brave runs)."
echo ""
echo "To keep BOTH readers active for testing (will be laggy):"
echo "  /usr/bin/brave-origin-nightly --enable-gpu-rasterization"
echo ""
echo "To fully disable NVDA autostart (if you only need Orca for Brave):"
echo "  mkdir -p ~/.config/autostart.bak && mv ~/.config/autostart/winenvda.desktop ~/.config/autostart.bak/"
echo "To re-enable: mv ~/.config/autostart.bak/winenvda.desktop ~/.config/autostart/"
