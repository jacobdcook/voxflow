#!/usr/bin/env bash
# mintflow Linux installer (apt-based: Ubuntu, Debian, Linux Mint).
set -euo pipefail

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/mintflow/venv"
BIN_DIR="$HOME/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

info()  { printf '\n==> %s\n' "$*"; }
ok()    { printf '    OK  %s\n' "$*"; }
warn()  { printf '    !!  %s\n' "$*"; }
die()   { printf '    ERROR  %s\n' "$*" >&2; exit 1; }

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  die "Do not run this as root. Run it as your normal user. The script will ask for sudo only for apt."
fi

python_ok() {
  local py="$1"
  command -v "$py" >/dev/null 2>&1 || return 1
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
}

info "Checking Python 3.10+"
PY=""
for candidate in python3 python; do
  if python_ok "$candidate"; then
    PY="$candidate"
    break
  fi
done

if [[ -z "$PY" ]]; then
  if command -v apt-get >/dev/null 2>&1; then
    warn "Python 3.10+ was not found. Installing python3 with apt."
    sudo apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-venv
    python_ok python3 || die "Python 3.10+ is still missing. Install it from https://www.python.org/downloads/ then re-run this script."
    PY=python3
  else
    die "Python 3.10+ is required. Install it, then re-run this script."
  fi
fi
ok "$PY $($PY -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

info "Installing system packages"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-pip \
    python3-venv \
    python3-gi \
    python3-gi-cairo \
    python3-xlib \
    gir1.2-gtk-3.0 \
    gir1.2-pango-1.0 \
    xclip \
    xdotool \
    libportaudio2 \
    portaudio19-dev
  ok "apt packages installed"
else
  warn "apt-get not found. Install these yourself: python3-gi python3-xlib xclip xdotool libportaudio2"
fi

info "Installing mintflow"
mkdir -p "$VENV" "$BIN_DIR"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PY" -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip wheel
if [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
  "$VENV/bin/python" -m pip install "$REPO_ROOT[linux]"
  ok "installed from this repo"
else
  "$VENV/bin/python" -m pip install "mintflow[linux]"
  ok "installed from PyPI"
fi
ln -sfn "$VENV/bin/mintflow" "$BIN_DIR/mintflow"

if ! printf ':%s:' "$PATH" | grep -q ":$BIN_DIR:"; then
  export PATH="$BIN_DIR:$PATH"
  for rc in "$HOME/.bashrc" "$HOME/.profile"; do
    if [[ -f "$rc" ]] && grep -q '.local/bin' "$rc" 2>/dev/null; then
      continue
    fi
    if [[ -f "$rc" ]] || [[ "$rc" == "$HOME/.profile" ]]; then
      printf '\n# mintflow\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$rc"
      ok "added $BIN_DIR to PATH in $rc"
      break
    fi
  done
  warn "If the mintflow command is not found, open a new terminal (PATH was updated)."
fi
ok "mintflow -> $BIN_DIR/mintflow"

info "Checking Ollama"
if command -v ollama >/dev/null 2>&1; then
  ok "ollama found"
  if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    warn "Ollama is installed but not answering yet. Starting it in the background."
    nohup ollama serve >/dev/null 2>&1 &
    sleep 2
  fi
  info "Pulling cleanup model $OLLAMA_MODEL (this can take a while the first time)"
  ollama pull "$OLLAMA_MODEL" || warn "ollama pull failed. Later run: ollama pull $OLLAMA_MODEL"
else
  warn "Ollama is not installed. Cleanup will use simple local rules until you install it:"
  warn "  https://ollama.com/download"
  warn "  then: ollama pull $OLLAMA_MODEL"
fi

info "First-run setup (GPU detect + hotkey)"
if command -v mintflow >/dev/null 2>&1; then
  MF=mintflow
else
  MF="$BIN_DIR/mintflow"
fi
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  warn "No graphical display. Skip GUI setup. Later run: mintflow setup"
else
  "$MF" setup || warn "setup did not finish. Later run: mintflow setup"
fi

info "Setting up autostart"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/autostart"
SERVICE="$HOME/.config/systemd/user/mintflow.service"
cat > "$SERVICE" <<EOF
[Unit]
Description=mintflow voice-to-text
After=graphical-session.target

[Service]
Type=simple
ExecStart=$VENV/bin/mintflow
Restart=on-failure
RestartSec=3
Environment=DISPLAY=${DISPLAY:-:0}

[Install]
WantedBy=default.target
EOF

DESKTOP="$HOME/.config/autostart/mintflow.desktop"
ICON_SRC="$REPO_ROOT/mintflow.svg"
ICON_LINE=""
if [[ -f "$ICON_SRC" ]]; then
  ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
  mkdir -p "$ICON_DIR"
  cp "$ICON_SRC" "$ICON_DIR/mintflow.svg"
  ICON_LINE="Icon=mintflow"
fi
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=mintflow
Comment=Hold a key, speak, release
Exec=$BIN_DIR/mintflow
Terminal=false
X-GNOME-Autostart-enabled=true
${ICON_LINE}
EOF

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  systemctl --user enable --now mintflow.service >/dev/null 2>&1 || {
    warn "systemd user service could not start. Starting mintflow in the background."
    nohup "$MF" >/dev/null 2>&1 &
  }
  ok "autostart enabled (systemd user service + desktop entry)"
else
  nohup "$MF" >/dev/null 2>&1 &
  ok "autostart desktop entry written"
fi

KEY="Pause"
CFG="$HOME/.config/mintflow/config.json"
if [[ -f "$CFG" ]]; then
  KEY="$("$PY" -c '
import json, pathlib, sys
p = pathlib.Path.home() / ".config" / "mintflow" / "config.json"
try:
    cfg = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("Pause")
    raise SystemExit
print(cfg.get("hotkey_label") or str(cfg.get("hotkey") or "pause").upper())
' 2>/dev/null || echo Pause)"
fi

printf '\nReady! Press %s to talk.\n' "$KEY"
printf 'Stop later with: mintflow quit\n'
