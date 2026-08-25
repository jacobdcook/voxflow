#!/usr/bin/env bash
###############################################################################
# auto_build_mintflow.sh — Build mintflow cross-platform package
#
# Pipeline:
#   Cursor/Grok 4.6  →  build phases A-G  →  push
#   Claude Opus 4.6  →  audit + fix (H)   →  push
#   Claude Opus      →  audit + fix (I)   →  push
#   Fable (if needed)→  audit + fix (J)   →  push + tag + release
#
# Usage:
#   cd /home/z1337/Desktop/PROJECTS/mintflow
#   bash auto_build_mintflow.sh
#   bash auto_build_mintflow.sh --list
#   bash auto_build_mintflow.sh --from D
#   CURSOR_MODEL=cursor-grok-4.6-high-fast bash auto_build_mintflow.sh
###############################################################################

set -euo pipefail

PROJECT="/home/z1337/Desktop/PROJECTS/mintflow"
PLAN="$PROJECT/PLAN.md"
STATE="$PROJECT/.auto_build_state.json"
LOG="$PROJECT/logs/auto_build_$(date +%Y%m%d_%H%M%S).log"

# Models — override with env vars
CURSOR_MODEL="${CURSOR_MODEL:-cursor-grok-4.6-high-fast}"
CLAUDE_MODEL_1="${CLAUDE_MODEL_1:-}"                        # Opus 4.6 (CLI default)
CLAUDE_MODEL_2="${CLAUDE_MODEL_2:-opus}"                    # latest Opus
FABLE_MODEL="${FABLE_MODEL:-claude-fable-5-thinking-xhigh}" # Cursor agent for Fable

COOLDOWN_SEC="${COOLDOWN_SEC:-20}"
GITHUB_USER="jacobdcook"
REPO_NAME="mintflow"

mkdir -p "$PROJECT/logs"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[$(date '+%H:%M:%S')]${NC} $1" | tee -a "$LOG"; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $1" | tee -a "$LOG"; }
ok()   { echo -e "${GREEN}[$(date '+%H:%M:%S')] DONE:${NC} $1" | tee -a "$LOG"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARN:${NC} $1" | tee -a "$LOG"; }

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

get_state() {
    if [[ -f "$STATE" ]]; then
        python3 -c "import json; d=json.load(open('$STATE')); print(d.get('$1','$2'))"
    else
        echo "$2"
    fi
}

set_state() {
    python3 -c "
import json, os
f='$STATE'
d = json.load(open(f)) if os.path.exists(f) else {}
d['$1'] = '$2'
d['last_updated'] = '$(date -Iseconds)'
json.dump(d, open(f, 'w'), indent=2)
"
}

# ---------------------------------------------------------------------------
# Agent runner (Cursor or Claude)
# ---------------------------------------------------------------------------

run_cursor() {
    local label="$1"
    local prompt="$2"
    local model="${3:-$CURSOR_MODEL}"
    local max_retries=8
    local attempt=0

    while (( attempt < max_retries )); do
        attempt=$((attempt + 1))
        log "${YELLOW}[$label]${NC} Attempt $attempt — Cursor agent (model=$model)"

        local session_log="$PROJECT/logs/mintflow_${label}_$(date +%H%M%S).log"
        local ok_flag=0

        if ! command -v agent >/dev/null 2>&1; then
            err "Cursor agent CLI not found. Install: npm i -g @anthropic-ai/cursor-agent"
            return 1
        fi

        set +e
        agent -p --force --trust --workspace "$PROJECT" \
            --model "$model" \
            --output-format text \
            "$prompt" \
            2>&1 | tee -a "$session_log" "$LOG"
        [[ "${PIPESTATUS[0]}" == "0" ]] && ok_flag=1
        set -e

        if (( ok_flag == 1 )); then
            ok "$label completed"
            return 0
        fi

        if grep -qi "Cannot use this model\|invalid model" "$session_log" 2>/dev/null; then
            err "Bad model: $model. Check 'agent models' for available models."
            return 1
        fi

        if grep -qi "usage\|limit\|rate\|quota\|capacity\|unauthorized" "$session_log" 2>/dev/null; then
            warn "Usage/auth issue. Waiting 30 minutes..."
            for i in $(seq 30 -1 1); do
                echo -ne "\r  Waiting... ${i}m remaining    "
                sleep 60
            done
            echo ""
        else
            err "$label failed (attempt $attempt)"
            if (( attempt >= 3 )); then
                warn "Trying Claude Code fallback..."
                run_claude "$label" "$prompt" ""
                return $?
            fi
            sleep 15
        fi
    done
    err "$label exhausted all retries"
    return 1
}

run_claude() {
    local label="$1"
    local prompt="$2"
    local model="${3:-}"
    local max_retries=5
    local attempt=0

    if ! command -v claude >/dev/null 2>&1; then
        err "Claude Code CLI not found"
        return 1
    fi

    local model_args=()
    if [[ -n "$model" ]]; then
        model_args=(--model "$model")
    fi

    while (( attempt < max_retries )); do
        attempt=$((attempt + 1))
        log "${YELLOW}[$label]${NC} Attempt $attempt — Claude Code ${model:+(model=$model)}"

        local session_log="$PROJECT/logs/mintflow_${label}_claude_$(date +%H%M%S).log"
        local ok_flag=0

        set +e
        claude -p "${model_args[@]}" --output-format text --verbose \
            "$prompt" \
            2>&1 | tee -a "$session_log" "$LOG"
        [[ $? == 0 ]] && ok_flag=1
        set -e

        if (( ok_flag == 1 )); then
            ok "$label completed (Claude)"
            return 0
        fi

        if grep -qi "usage\|limit\|rate\|quota" "$session_log" 2>/dev/null; then
            warn "Usage limit. Waiting 60 minutes..."
            for i in $(seq 60 -1 1); do
                echo -ne "\r  Waiting... ${i}m remaining    "
                sleep 60
            done
            echo ""
        else
            err "$label failed (attempt $attempt)"
            sleep 15
        fi
    done
    err "$label exhausted all retries"
    return 1
}

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

ensure_repo() {
    cd "$PROJECT"
    if [[ ! -d .git ]]; then
        log "Initializing git repo..."
        git init
        git checkout -b main
        cat > .gitignore << 'GITEOF'
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.auto_build_state.json
logs/
*.log
.env
*.wav
*.mp3
mintflow_v0.py
GITEOF
        git add .gitignore
        git -c core.hooksPath=/tmp/nohooks commit -m "Initial commit"
    fi

    # Create GitHub repo if needed
    if ! git remote get-url origin >/dev/null 2>&1; then
        log "Creating GitHub repo: $GITHUB_USER/$REPO_NAME"
        if gh repo view "$GITHUB_USER/$REPO_NAME" >/dev/null 2>&1; then
            log "Repo exists, adding remote"
            git remote add origin "https://github.com/$GITHUB_USER/$REPO_NAME.git"
        else
            gh repo create "$REPO_NAME" \
                --public \
                --description "Voice to text, everywhere. Hold a key, speak, release. Open source Wispr Flow alternative." \
                --source . \
                --push \
                || {
                    warn "gh repo create failed — trying manual remote add"
                    git remote add origin "https://github.com/$GITHUB_USER/$REPO_NAME.git"
                    git push -u origin main
                }
        fi
    fi
}

push_changes() {
    cd "$PROJECT"
    # Always push — the agent may have committed already (clean working tree but ahead of origin)
    git fetch origin 2>&1 | tee -a "$LOG" || true
    git pull origin main --no-rebase --no-edit 2>&1 | tee -a "$LOG" || true
    git push origin HEAD 2>&1 | tee -a "$LOG" || true
    ok "Pushed to origin"
}

# ---------------------------------------------------------------------------
# Prompt wrapper
# ---------------------------------------------------------------------------

wrap_prompt() {
    local letter="$1"
    local title="$2"
    local body="$3"
    cat <<PROMPT_EOF
You are building mintflow — a cross-platform, open-source voice-to-text tool (like Wispr Flow).
Hold a key, speak, release. Cleaned text pastes into whatever app is focused.
Runs locally: Whisper for STT, Ollama/Qwen for text cleanup. No cloud, no account.

Project: $PROJECT
Full spec: $PROJECT/PLAN.md — READ THIS FIRST. It has the complete architecture, module specs, overlay design, and critical implementation notes.
Reference: $PROJECT/mintflow_v0.py — the working Linux prototype. Port logic from here.

=== Phase $letter: $title ===

$body

=== Rules ===
- Read PLAN.md first. It answers most architecture questions.
- Reference mintflow_v0.py for proven Linux code to port.
- No Co-authored-by or AI attribution in commits.
- Stage only files you changed: git add <specific files>
- Commit: git -c core.hooksPath=/tmp/nohooks commit -m "$title"
- Do NOT ask questions. Implement fully.
- If a file already exists and looks correct, skip it. Say ALREADY DONE.
- py_compile every .py file you create before committing.
PROMPT_EOF
}

# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

BODY_A='Create the package foundation. Files to create:

1. pyproject.toml — name mintflow, version 1.0.0, Python >=3.10, deps (numpy, sounddevice, faster-whisper, httpx), optional-deps for linux (python-xlib) and desktop (pynput, pyperclip). Entry point: mintflow = mintflow.cli:main. Include project metadata: description, license MIT, author Jacob Cook, repo URL https://github.com/jacobdcook/mintflow, classifiers, keywords.

2. LICENSE — MIT license, copyright 2024 Jacob Cook.

3. mintflow/__init__.py — just __version__ = "1.0.0"

4. mintflow/__main__.py — from mintflow.cli import main; main()

5. mintflow/config.py — See PLAN.md config.py section. Platform-aware paths (Linux ~/.config/mintflow, macOS ~/Library/Application Support/mintflow, Windows %APPDATA%/mintflow). Load/save JSON config. load_vocabulary(). STYLE_HINTS dict. JUNK_TRANSCRIPTS set. PID file path. log() function.

6. mintflow/gpu.py — See PLAN.md gpu.py section. detect_gpu(), recommend_model(), setup_auto_config(). RAM detection per platform (/proc/meminfo, sysctl, wmic).'

BODY_B='Create core engine modules. Files to create:

1. mintflow/audio.py — Port Recorder class from mintflow_v0.py. Add get_snapshot() method (thread-safe copy of audio buffer without stopping recording). See PLAN.md audio.py section.

2. mintflow/cleanup.py — Port and IMPROVE from mintflow_v0.py. CRITICAL: use /api/chat NOT /api/generate for Ollama. Include REWRITE_SYSTEM prompt, TERMINAL_ADDON, REWRITE_EXAMPLES (3 few-shot pairs), _sane_rewrite validator, local_cleanup fallback with all regex patterns (FILLERS, SPOKEN_PUNCT, SCRATCH, SELF_CORRECT). See PLAN.md cleanup.py section and critical notes.

3. mintflow/engine.py — Whisper engine. CUDA preload function from v0. Engine class with preload(), transcribe(fast=True for streaming / False for final), rewrite(). Hallucination filtering. See PLAN.md engine.py section.'

BODY_C='Create the Linux platform backend. This is the biggest file — port from mintflow_v0.py.

File: mintflow/platform/linux.py

Must implement the Backend interface from PLAN.md app.py section. Port:
- HotkeyGrabber (Xlib X.GrabModeAsync, autorepeat debounce, tap vs hold)
- Overlay (GTK3 POPUP window, custom Cairo drawing, waveform bars)
- NEW: Streaming text display in overlay using PangoCairo (word-wrapped text below waveform, overlay expands when text appears). See PLAN.md overlay design section.
- Clipboard (xclip)
- Paste injection (xdotool, Ctrl+V / Ctrl+Shift+V for terminals)
- Terminal detection (xprop WM_CLASS)
- Sound (paplay with freedesktop sounds)
- Capture hotkey dialog (GTK3 window that captures keypress)

Also create mintflow/platform/__init__.py — OS detection, get_backend() factory.

This is the critical path — the Linux backend must work perfectly since we can test it on this machine.'

BODY_D='Create the macOS platform backend.

File: mintflow/platform/macos.py

Implement same Backend interface as Linux. Uses:
- pynput keyboard.Listener with suppress for hotkey (CGEventTap)
- pynput keyboard.Controller for tap replay
- tkinter Toplevel for overlay (overrideredirect, -topmost, Canvas for waveform + text)
- Match the dark pill visual design from PLAN.md overlay section
- subprocess pbcopy/pbpaste for clipboard
- osascript for paste (Cmd+V via System Events)
- osascript for terminal detection (check process name for Terminal, iTerm2, Alacritty, kitty, WezTerm)
- afplay for sounds (use /System/Library/Sounds/ if available)
- tkinter capture hotkey dialog

Read the Linux backend for reference on the state machine callbacks and overlay drawing logic. The overlay should look identical (dark rounded rect, cyan/yellow/green/red bars, white text).'

BODY_E='Create the Windows platform backend.

File: mintflow/platform/windows.py

Implement same Backend interface. Uses:
- pynput keyboard.Listener with suppress for hotkey
- pynput keyboard.Controller for tap replay and paste (Ctrl+V)
- tkinter Toplevel for overlay (same design as macOS)
- pyperclip for clipboard (or ctypes win32 as fallback)
- Terminal detection: check foreground window class for ConsoleWindowClass, CASCADIA_HOSTING_WINDOW_CLASS, mintty, etc. Use ctypes + user32.dll GetForegroundWindow + GetClassName.
- winsound.PlaySound or subprocess powershell for sounds
- tkinter capture hotkey dialog

Read the macOS backend — Windows is very similar (both use pynput + tkinter). Main difference is paste mechanism and terminal detection.'

BODY_F='Create the app orchestrator and CLI. These tie everything together.

1. mintflow/app.py — FlowApp class. Platform-agnostic state machine. See PLAN.md app.py section.
States: idle, armed, listening, handsfree_stop, transcribing, cleaning.
Key features:
- Streaming transcription thread (runs during listening, calls get_snapshot + transcribe(fast=True) every stream_interval_s, pushes text to overlay)
- Confirmed text prefix locking for long recordings (>30s)
- Hands-free mode (tap toggle for non-typing keys)
- Tap-through for typing keys (replay the character)
- Terminal detection before cleanup (changes cleanup behavior)

2. mintflow/cli.py — main() entry point. Subcommands: run (default), quit/stop, setup, set-hotkey, test-mic, test-inject, demo, models, help. PID file management. Signal handlers. First-run detection (if config has "auto" for model, run setup).

Read mintflow_v0.py for the proven state machine logic. The app.py should be a clean extraction that delegates all platform calls to the Backend interface.'

BODY_G='Create documentation and release infrastructure. Make this look like a real product.

1. README.md — See PLAN.md README section. Must be readable by non-programmers. Include:
   - Badges (license, platform, Python version)
   - Hero description (2 lines max)
   - Animated GIF placeholder: ![demo](demo.gif) with note "demo coming soon"
   - "How it works" (hold key, speak, release — 15 words)
   - Install sections for macOS, Windows, Linux (numbered steps, copy-paste commands)
   - "Set your hotkey" section
   - "Settings" table
   - "Custom vocabulary" section
   - "GPU & model" section (mintflow models command)
   - "Privacy" section (everything local, nothing leaves your machine)
   - Troubleshooting FAQ
   - Contributing section
   - License

2. scripts/install-linux.sh — See PLAN.md. Check Python 3.10+, install apt deps, pip install, check ollama, pull model, run setup.
3. scripts/install-mac.sh — Same pattern for macOS (brew).
4. scripts/install-win.ps1 — Same pattern for Windows (winget/choco).

5. .github/workflows/release.yml — On tag push v*, build sdist+wheel, create GitHub Release with assets.

Make the README polished. Use clean markdown, proper headings, code blocks with language tags. This is the first thing people see.'

BODY_H='AUDIT PHASE (Opus 4.6). You are auditing the mintflow cross-platform package.

Read every file in the mintflow/ package directory. Check against PLAN.md spec.

Fix these categories:
1. BUGS — logic errors, race conditions, missing error handling, import errors
2. CROSS-PLATFORM — things that will crash on macOS or Windows (os.getuid, Linux-only paths, missing platform checks)
3. STREAMING — verify the streaming transcription thread is correct (get_snapshot, thread safety, confirmed text locking)
4. CLEANUP PIPELINE — verify /api/chat is used (NOT /api/generate), sanity check exists, few-shot examples present
5. OVERLAY — verify text display works (PangoCairo on Linux, Canvas text on Mac/Win)
6. STATE MACHINE — verify tap vs hold, hands-free toggle, all state transitions
7. IMPORTS — verify no circular imports, all platform modules import cleanly on their target OS
8. PACKAGING — verify pyproject.toml entry points, dependencies complete

Run py_compile on every .py file. Fix any syntax errors.
Test on this Linux machine: pip install -e . && mintflow models && mintflow test-mic

Stage fixes, commit: git -c core.hooksPath=/tmp/nohooks commit -m "Opus 4.6 audit fixes"'

BODY_I='AUDIT PHASE (Opus 4.8 / latest). Second-pass audit of mintflow.

Focus on what the first audit may have missed:
1. EDGE CASES — empty audio, very long recordings (>5min), rapid tap sequences, hotkey while processing
2. SECURITY — no command injection in subprocess calls, clipboard restore race conditions
3. UX POLISH — overlay positioning on multi-monitor, sound volume, error messages helpful for non-programmers
4. README — grammar, accuracy, do install instructions actually work, are steps numbered correctly
5. PERFORMANCE — is streaming interval tuned right, does GPU detection cache results, unnecessary work in hot paths
6. CONFIG MIGRATION — if user has old v0 config, does it load without crashing

Run the full test: pip install -e . && mintflow models && mintflow demo && mintflow test-mic
Fix, commit: git -c core.hooksPath=/tmp/nohooks commit -m "Second audit fixes"'

BODY_J='FINAL AUDIT (Fable). Read the two previous audit commits to see what was fixed.

Only run if previous audits found significant issues. If the code looks solid:
- Run py_compile on all files
- Run mintflow test-mic
- If everything passes, say LOOKS GOOD and exit 0 without committing

If issues remain:
- Fix them
- Commit: git -c core.hooksPath=/tmp/nohooks commit -m "Final audit fixes"'

# ---------------------------------------------------------------------------
# Prompt/phase arrays
# ---------------------------------------------------------------------------

PROMPTS=(A B C D E F G H I J)

declare -A TITLES BODIES RUNNERS MODELS
TITLES[A]="Package Foundation"
TITLES[B]="Core Engine Modules"
TITLES[C]="Linux Platform Backend"
TITLES[D]="macOS Platform Backend"
TITLES[E]="Windows Platform Backend"
TITLES[F]="App Orchestrator + CLI"
TITLES[G]="Documentation + Release"
TITLES[H]="Opus 4.6 Audit"
TITLES[I]="Second Audit"
TITLES[J]="Final Audit (Fable)"

BODIES[A]="$BODY_A"
BODIES[B]="$BODY_B"
BODIES[C]="$BODY_C"
BODIES[D]="$BODY_D"
BODIES[E]="$BODY_E"
BODIES[F]="$BODY_F"
BODIES[G]="$BODY_G"
BODIES[H]="$BODY_H"
BODIES[I]="$BODY_I"
BODIES[J]="$BODY_J"

# A-G: Cursor/Grok for implementation
# H: Claude Opus 4.6 audit
# I: Claude latest Opus audit
# J: Cursor Fable audit (conditional)
RUNNERS[A]="cursor"
RUNNERS[B]="cursor"
RUNNERS[C]="cursor"
RUNNERS[D]="cursor"
RUNNERS[E]="cursor"
RUNNERS[F]="cursor"
RUNNERS[G]="cursor"
RUNNERS[H]="claude"
RUNNERS[I]="claude"
RUNNERS[J]="cursor"

MODELS[A]="$CURSOR_MODEL"
MODELS[B]="$CURSOR_MODEL"
MODELS[C]="$CURSOR_MODEL"
MODELS[D]="$CURSOR_MODEL"
MODELS[E]="$CURSOR_MODEL"
MODELS[F]="$CURSOR_MODEL"
MODELS[G]="$CURSOR_MODEL"
MODELS[H]="$CLAUDE_MODEL_1"
MODELS[I]="$CLAUDE_MODEL_2"
MODELS[J]="$FABLE_MODEL"

# ---------------------------------------------------------------------------
# Board display
# ---------------------------------------------------------------------------

print_board() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}MINTFLOW BUILD QUEUE${NC}                        ${CYAN}║${NC}"
    echo -e "${CYAN}╠══════════════════════════════════════════════╣${NC}"
    local letter st runner
    for letter in "${PROMPTS[@]}"; do
        st=$(get_state "completed_$letter" "false")
        runner="${RUNNERS[$letter]}"
        local icon="🔨"
        [[ "$runner" == "claude" ]] && icon="🔍"
        [[ "$letter" == "J" ]] && icon="✨"
        if [[ "$st" == "true" ]]; then
            printf "${CYAN}║${NC}  ${GREEN}✓${NC}  %s  %-30s ${GREEN}done${NC}  ${CYAN}║${NC}\n" "$letter" "${TITLES[$letter]}"
        else
            printf "${CYAN}║${NC}  ${YELLOW}○${NC}  %s  %-30s %s     ${CYAN}║${NC}\n" "$letter" "${TITLES[$letter]}" "$icon"
        fi
    done
    echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
    echo ""
}

# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

do_release() {
    cd "$PROJECT"
    log "Creating v1.0.0 release..."

    git tag -d v1.0.0 2>/dev/null || true
    git push origin :refs/tags/v1.0.0 2>&1 | tee -a "$LOG" || true
    git tag -a v1.0.0 -m "mintflow v1.0.0 — cross-platform voice-to-text"
    git push origin v1.0.0 2>&1 | tee -a "$LOG"

    log "Tag pushed. Waiting for Release workflow..."
    local i
    for i in $(seq 1 30); do
        sleep 20
        local status
        status="$(gh run list --repo "$GITHUB_USER/$REPO_NAME" --workflow=Release --limit 1 --json conclusion,status --jq '.[0] | "\(.status) \(.conclusion)"' 2>/dev/null || echo "unknown")"
        log "Release: $status"
        if echo "$status" | grep -q "completed success"; then
            ok "GitHub Release created"
            gh release view v1.0.0 --repo "$GITHUB_USER/$REPO_NAME" 2>&1 | tee -a "$LOG" || true
            return 0
        fi
        if echo "$status" | grep -q "completed failure\|completed cancelled"; then
            warn "Release workflow failed. Creating release manually..."
            gh release create v1.0.0 \
                --repo "$GITHUB_USER/$REPO_NAME" \
                --title "mintflow v1.0.0" \
                --notes "First cross-platform release. See README for install instructions." \
                2>&1 | tee -a "$LOG" || true
            return 0
        fi
    done
    warn "Timed out waiting for workflow. Creating release manually..."
    gh release create v1.0.0 \
        --repo "$GITHUB_USER/$REPO_NAME" \
        --title "mintflow v1.0.0" \
        --notes "First cross-platform release. See README for install instructions." \
        2>&1 | tee -a "$LOG" || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

cd "$PROJECT"

FROM_ARG=""
LIST_ONLY=0
SKIP_FABLE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --list) LIST_ONLY=1; shift ;;
        --from) FROM_ARG="${2:-}"; shift 2 ;;
        --skip-fable) SKIP_FABLE=1; shift ;;
        -h|--help)
            sed -n '2,16p' "$0"
            exit 0
            ;;
        *) err "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ ! -f "$STATE" ]]; then
    echo '{"current_prompt":"A"}' > "$STATE"
fi

if [[ -n "$FROM_ARG" ]]; then
    set_state "current_prompt" "$FROM_ARG"
    set_state "completed_$FROM_ARG" "false"
fi

if (( LIST_ONLY == 1 )); then
    echo -e "${BOLD}mintflow build queue${NC}"
    echo "Cursor model: $CURSOR_MODEL"
    print_board
    exit 0
fi

# Preflight
log "Preflight checks..."
command -v python3 >/dev/null || { err "python3 not found"; exit 1; }
command -v git >/dev/null || { err "git not found"; exit 1; }
command -v gh >/dev/null || { err "gh CLI not found (brew install gh)"; exit 1; }

if ! command -v agent >/dev/null 2>&1 && ! command -v claude >/dev/null 2>&1; then
    err "Neither 'agent' (Cursor) nor 'claude' (Claude Code) found"
    exit 1
fi

echo ""
echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN}  mintflow cross-platform build${NC}"
echo -e "${GREEN}  Cursor model: $CURSOR_MODEL${NC}"
echo -e "${GREEN}  Log: $LOG${NC}"
echo -e "${GREEN}================================================================${NC}"
print_board

# Init git + GitHub repo
ensure_repo

# Add PLAN.md and existing files
cd "$PROJECT"
if [[ -n "$(git status --porcelain PLAN.md mintflow.svg 2>/dev/null)" ]]; then
    git add PLAN.md mintflow.svg .gitignore
    git -c core.hooksPath=/tmp/nohooks commit -m "Add build plan and assets" || true
    push_changes
fi

# Find starting point
CURRENT=$(get_state "current_prompt" "A")
if [[ "$(get_state "completed_$CURRENT" false)" == "true" ]]; then
    for P in "${PROMPTS[@]}"; do
        if [[ "$(get_state "completed_$P" false)" != "true" ]]; then
            CURRENT="$P"
            set_state "current_prompt" "$P"
            break
        fi
    done
fi
log "Resuming from: $CURRENT"

# ---------------------------------------------------------------------------
# Run phases
# ---------------------------------------------------------------------------

STARTED=false
for P in "${PROMPTS[@]}"; do
    if [[ "$STARTED" == false && "$P" != "$CURRENT" ]]; then
        continue
    fi
    STARTED=true

    if [[ "$(get_state "completed_$P" false)" == "true" ]]; then
        log "Skip $P (done)"
        continue
    fi

    # Skip Fable if --skip-fable or if previous audits were clean
    if [[ "$P" == "J" ]]; then
        if (( SKIP_FABLE == 1 )); then
            log "Skipping Fable audit (--skip-fable)"
            set_state "completed_J" "true"
            continue
        fi
        audit_diff=$(git log -1 --format="%s" 2>/dev/null || echo "")
        if echo "$audit_diff" | grep -qi "LOOKS GOOD\|no changes\|already done"; then
            log "Previous audit was clean — skipping Fable"
            set_state "completed_J" "true"
            continue
        fi
    fi

    letter="$P"
    runner="${RUNNERS[$letter]}"
    model="${MODELS[$letter]}"

    print_board
    log "${YELLOW}========== Phase $letter: ${TITLES[$letter]} ==========${NC}"
    log "Runner: $runner | Model: ${model:-default}"
    set_state "current_prompt" "$letter"

    prompt_text="$(wrap_prompt "$letter" "${TITLES[$letter]}" "${BODIES[$letter]}")"

    phase_ok=0
    if [[ "$runner" == "cursor" ]]; then
        run_cursor "Phase-$letter" "$prompt_text" "$model" && phase_ok=1
    else
        run_claude "Phase-$letter" "$prompt_text" "$model" && phase_ok=1
    fi

    if (( phase_ok == 1 )); then
        set_state "completed_$letter" "true"
        ok "Phase $letter done"
        push_changes
    else
        err "Phase $letter failed — re-run to resume from here"
        print_board
        exit 1
    fi

    log "Cooldown ${COOLDOWN_SEC}s..."
    sleep "$COOLDOWN_SEC"
done

set_state "current_prompt" "DONE"
ok "All phases complete"
print_board

# Tag and release
do_release

echo ""
echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN}  mintflow v1.0.0 shipped!${NC}"
echo -e "${GREEN}  https://github.com/$GITHUB_USER/$REPO_NAME${NC}"
echo -e "${GREEN}================================================================${NC}"
echo ""
echo "Install:  pip install git+https://github.com/$GITHUB_USER/$REPO_NAME"
echo "Or:       cd $PROJECT && pip install -e ."
echo "Test:     mintflow test-mic"
echo "Run:      mintflow"
