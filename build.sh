#!/usr/bin/env bash
# Quiet build for macOS (.app + .dmg, then installs) and Linux (installs to ~/.local).
set -euo pipefail
cd "$(dirname "$0")"
LOG=build.log
: > "$LOG"

run() {
  if ! "$@" >> "$LOG" 2>&1; then
    echo; echo "Build failed. Last lines of $LOG:"; tail -n 20 "$LOG"; exit 1
  fi
}

echo "[1/4] Setting up Python environment"
[ -d .venv ] || run "${PYTHON:-python3}" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
run pip install -q --disable-pip-version-check -r requirements.txt

echo "[2/4] Generating icons"
run python make_icons.py

echo "[3/4] Building app"
run pyinstaller --noconfirm --clean --log-level ERROR MrBAI.spec

if [[ "$OSTYPE" == darwin* ]]; then
  echo "[4/4] Creating and running installer"
  mkdir -p installer "$HOME/Applications"
  rm -f installer/MrBAI.dmg
  run hdiutil create -quiet -volname "Mr. B AI" -srcfolder "dist/Mr. B AI.app" -ov -format UDZO installer/MrBAI.dmg
  MNT="$(mktemp -d)"
  run hdiutil attach -quiet -nobrowse -mountpoint "$MNT" installer/MrBAI.dmg
  rm -rf "$HOME/Applications/Mr. B AI.app"
  run ditto "$MNT/Mr. B AI.app" "$HOME/Applications/Mr. B AI.app"
  run hdiutil detach -quiet "$MNT"
  echo "Done. Installed to ~/Applications."
  open "$HOME/Applications/Mr. B AI.app"
else
  echo "[4/4] Installing to ~/.local"
  DEST="$HOME/.local/opt/MrBAI"
  rm -rf "$DEST"; mkdir -p "$DEST" "$HOME/.local/share/applications"
  cp -r dist/MrBAI/. "$DEST/"
  cp assets/icon.png "$DEST/icon.png"
  cat > "$HOME/.local/share/applications/mrbai.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Mr. B AI
Exec=$DEST/MrBAI
Icon=$DEST/icon.png
Terminal=false
Categories=Education;Utility;
DESK
  echo "Done. Find Mr. B AI in your app menu, or run $DEST/MrBAI"
  nohup "$DEST/MrBAI" >/dev/null 2>&1 &
fi
