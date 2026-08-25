#!/usr/bin/env bash
# voxflow macOS installer (Homebrew).
set -euo pipefail

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
VENV="$HOME/.local/share/voxflow/venv"
BIN_DIR="$HOME/.local/bin"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS/com.voxflow.app.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

info()  { printf '\n==> %s\n' "$*"; }
ok()    { printf '    OK  %s\n' "$*"; }
warn()  { printf '    !!  %s\n' "$*"; }
die()   { printf '    ERROR  %s\n' "$*" >&2; exit 1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This installer is for macOS. Use scripts/install-linux.sh or scripts/install-win.ps1."
fi

python_ok() {
  local py="$1"
  command -v "$py" >/dev/null 2>&1 || return 1
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
}

info "Checking Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  die "Homebrew is not installed. Paste this in Terminal, then re-run this script:
  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
fi
ok "brew $(brew --version | head -n1)"

info "Checking Python 3.10+"
PY=""
for candidate in python3 python; do
  if python_ok "$candidate"; then
    PY="$candidate"
    break
  fi
done

if [[ -z "$PY" ]]; then
  warn "Python 3.10+ was not found. Installing with Homebrew."
  brew install python
  python_ok python3 || die "Python 3.10+ is still missing. Install it from https://www.python.org/downloads/ then re-run this script."
  PY=python3
fi
ok "$PY $($PY -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

info "Installing PortAudio, Tk, and Ollama (if needed)"
brew install python portaudio >/dev/null
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
  brew install python-tk python-tk@3 2>/dev/null || brew install python-tk@3.12 python-tk@3.13 2>/dev/null || \
    warn "Could not install python-tk. If the overlay fails, run: brew install python-tk"
fi
if ! command -v ollama >/dev/null 2>&1; then
  brew install ollama || warn "Could not install Ollama with brew. Download it from https://ollama.com/download"
fi
ok "system dependencies"

info "Installing voxflow"
mkdir -p "$VENV" "$BIN_DIR"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip wheel
if [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
  "$VENV/bin/python" -m pip install "$REPO_ROOT[desktop]"
  ok "installed from this repo"
else
  "$VENV/bin/python" -m pip install "voxflow[desktop]"
  ok "installed from PyPI"
fi
ln -sfn "$VENV/bin/voxflow" "$BIN_DIR/voxflow"

if ! printf ':%s:' "$PATH" | grep -q ":$BIN_DIR:"; then
  export PATH="$BIN_DIR:$PATH"
  for rc in "$HOME/.zprofile" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [[ -f "$rc" ]] && grep -q '.local/bin' "$rc" 2>/dev/null; then
      continue
    fi
    touch "$rc"
    printf '\n# voxflow\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$rc"
    ok "added $BIN_DIR to PATH in $rc"
    break
  done
  warn "If the voxflow command is not found, open a new terminal (PATH was updated)."
fi
ok "voxflow -> $BIN_DIR/voxflow"

info "Checking Ollama"
if command -v ollama >/dev/null 2>&1; then
  ok "ollama found"
  if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    warn "Starting Ollama in the background."
    open -a Ollama >/dev/null 2>&1 || nohup ollama serve >/dev/null 2>&1 &
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
if command -v voxflow >/dev/null 2>&1; then
  MF=voxflow
else
  MF="$BIN_DIR/voxflow"
fi
"$MF" setup || warn "setup did not finish. Later run: voxflow setup"

info "Setting up login autostart (launchd)"
mkdir -p "$LAUNCH_AGENTS"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.voxflow.app</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/voxflow</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
</dict>
</plist>
EOF

UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}/com.voxflow.app" >/dev/null 2>&1 || true
if launchctl bootstrap "gui/${UID_NUM}" "$PLIST" >/dev/null 2>&1; then
  ok "launchd agent loaded"
else
  launchctl load -w "$PLIST" >/dev/null 2>&1 || nohup "$MF" >/dev/null 2>&1 &
  ok "autostart registered"
fi

warn "macOS will ask for Microphone and Accessibility permission. Allow both or voxflow cannot hear you or paste text."

KEY="Pause"
CFG="$HOME/Library/Application Support/voxflow/config.json"
if [[ -f "$CFG" ]]; then
  KEY="$("$PY" -c '
import json, pathlib
p = pathlib.Path.home() / "Library" / "Application Support" / "voxflow" / "config.json"
try:
    cfg = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("Pause")
    raise SystemExit
print(cfg.get("hotkey_label") or str(cfg.get("hotkey") or "pause").upper())
' 2>/dev/null || echo Pause)"
fi

printf '\nReady! Press %s to talk.\n' "$KEY"
printf 'Stop later with: voxflow quit\n'
