# mintflow — Cross-Platform Voice-to-Text (Wispr Flow for Everyone)

## What It Is

Hold a key, speak, release. Cleaned text pastes into whatever app you're in.
Local-only: Whisper (STT) + Ollama/Qwen (cleanup) on your own GPU or CPU.
No account, no cloud, no word cap.

Like Wispr Flow, but open-source and runs on Linux, macOS, and Windows.

## Current State

- `mintflow_v0.py` — working 1300-line Linux/X11 prototype (GTK3 overlay, Xlib hotkey grab, xclip paste)
- Runs on Jacob's machine: RTX 4090, Linux Mint, Cinnamon/X11
- Config at `~/.config/mintflow/config.json`
- Vocabulary at `~/.config/mintflow/vocabulary.txt`
- Autostart desktop entry at `~/.config/autostart/mintflow.desktop`

## Target: v1.0.0 Cross-Platform Package

### Package Structure

```
mintflow/                    # repo root
├── pyproject.toml           # pip install mintflow
├── README.md                # non-programmer install guide per OS
├── LICENSE                  # MIT
├── PLAN.md                  # this file
├── mintflow_v0.py           # original prototype (reference only)
├── mintflow.svg             # app icon
├── mintflow/                # Python package
│   ├── __init__.py          # __version__
│   ├── __main__.py          # python -m mintflow
│   ├── cli.py               # arg parsing: run, quit, setup, set-hotkey, test-mic, test-inject
│   ├── config.py            # load/save config, vocabulary, paths, defaults, STYLE_HINTS
│   ├── gpu.py               # detect GPU vendor/VRAM → recommend model+device+compute_type
│   ├── audio.py             # Recorder class (sounddevice, cross-platform)
│   ├── cleanup.py           # local_cleanup, ollama_rewrite, REWRITE_SYSTEM prompt, sanity check
│   ├── engine.py            # Whisper transcription + streaming support
│   ├── app.py               # FlowApp orchestrator (platform-agnostic state machine)
│   └── platform/
│       ├── __init__.py      # detect OS → import correct backend
│       ├── linux.py         # Xlib hotkey, GTK3 overlay with text, xclip paste
│       ├── macos.py         # pynput hotkey, tkinter overlay with text, pbcopy paste
│       └── windows.py       # pynput hotkey, tkinter overlay with text, win32 paste
├── scripts/
│   ├── install-linux.sh
│   ├── install-mac.sh
│   └── install-win.ps1
└── .github/
    └── workflows/
        └── release.yml
```

### Dependencies

**Core (all platforms):**
- numpy>=1.24
- sounddevice>=0.4
- faster-whisper>=1.0
- httpx>=0.24

**Linux extras (system packages, not pip):**
- python3-gi (PyGObject/GTK3)
- python3-xlib or pip python-xlib>=0.33
- xclip, xdotool

**macOS/Windows extras (pip):**
- pynput>=1.7
- pyperclip>=1.8

---

## Module Specifications

### config.py

Platform-aware config paths:
- Linux: `~/.config/mintflow/`
- macOS: `~/Library/Application Support/mintflow/`
- Windows: `%APPDATA%/mintflow/`

Default config:
```json
{
  "hotkey": "pause",
  "handsfree_max_s": 180,
  "model": "auto",
  "device": "auto",
  "compute_type": "auto",
  "language": "en",
  "style": "casual",
  "cleanup": "ollama",
  "ollama_model": "qwen2.5:7b",
  "ollama_url": "http://127.0.0.1:11434",
  "sample_rate": 16000,
  "tap_ms": 220,
  "repeat_ms": 80,
  "min_seconds": 0.35,
  "sounds": true,
  "restore_clipboard_ms": 450,
  "stream_interval_s": 1.0
}
```

When `model`/`device`/`compute_type` are `"auto"`, `gpu.py` detects hardware and fills them on first run.

`load_vocabulary()` reads `vocabulary.txt` from config dir (one term per line, # comments).

STYLE_HINTS dict: formal, casual, very-casual.

JUNK_TRANSCRIPTS set: common Whisper hallucinations to drop ("thank you", "thanks for watching", etc).

PID file at platform temp dir.

### gpu.py

`detect_gpu() -> GPUInfo` — returns vendor (nvidia/apple/amd/cpu), name, vram_gb, ram_gb.

Detection order:
1. `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` → parse
2. macOS + arm64 → Apple Silicon, RAM from `sysctl -n hw.memsize`
3. `rocm-smi` exists → AMD ROCm
4. Fallback: CPU, RAM from /proc/meminfo or sysctl or wmic

`recommend_model(gpu) -> ModelConfig` — returns model name, device, compute_type:

| Vendor | VRAM/RAM | Model | Device | Compute |
|--------|----------|-------|--------|---------|
| NVIDIA ≥6GB | large-v3 | cuda | float16 |
| NVIDIA ≥3GB | medium | cuda | float16 |
| NVIDIA <3GB | small | cuda | float16 |
| Apple Silicon | large-v3 | cpu | int8 |
| AMD ROCm | medium | cpu | int8 |
| CPU ≥16GB RAM | medium | cpu | int8 |
| CPU ≥8GB RAM | small | cpu | int8 |
| CPU <8GB RAM | base | cpu | int8 |

`setup_auto_config(cfg)` — if model/device/compute_type are "auto", detect GPU, fill config, save, print what was chosen.

### audio.py

Port the `Recorder` class from v0. Uses `sounddevice.InputStream`.

Key addition: `get_snapshot() -> np.ndarray` — thread-safe method that returns a COPY of all recorded audio so far WITHOUT stopping recording. Used by the streaming transcription thread.

```python
def get_snapshot(self) -> np.ndarray:
    with self._lock:
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks)
```

### cleanup.py

Port from v0 with improvements:

- `REWRITE_SYSTEM` — system prompt that prevents the LLM from answering questions in the transcript
- `TERMINAL_ADDON` — extra instruction when dictating into a terminal (no trailing period, preserve command syntax)
- `REWRITE_EXAMPLES` — 3 few-shot examples (messy → clean)
- `FILLERS`, `SPOKEN_PUNCT`, `SCRATCH`, `SELF_CORRECT` regex patterns
- `local_cleanup(text, terminal=False)` — regex-based fallback
- `ollama_rewrite(text, cfg, terminal=False)` — LLM rewrite via /api/chat (NOT /api/generate). Uses system prompt + few-shot examples
- `_sane_rewrite(out, raw)` — validates LLM output isn't garbage (refusal detection, length ratio check)

The LLM cleanup uses `/api/chat` with messages (system + few-shot + user), NOT `/api/generate` with a single prompt. This is critical for quality.

### engine.py

**Whisper engine:**
- `Engine.__init__(model_name, device, compute_type, language)` — stores config
- `Engine.preload()` — loads model + warms up Ollama (call from background thread)
- `Engine.transcribe(audio, fast=False, vocab_prompt="")` — full transcription
  - `fast=True`: beam_size=1 for streaming preview
  - `fast=False`: beam_size=5, best_of=5 for final quality
  - Filters hallucination segments (no_speech_prob > 0.66 + avg_logprob < -1.0)
  - Drops JUNK_TRANSCRIPTS
- `Engine.rewrite(text, terminal=False)` — cleanup pipeline (try ollama, fallback to local)

**CUDA preload function** — from v0. Loads NVIDIA .so files from pip site-packages if system CUDA isn't found.

### app.py — THE CORE ORCHESTRATOR

Platform-agnostic state machine. Does NOT import GTK, Xlib, tkinter, pynput directly. Talks to the platform backend through a `Backend` interface.

**Backend interface** (what each platform module provides):

```python
class Backend:
    # Main loop
    def run(self): ...                          # start event loop (blocking)
    def quit(self): ...                         # exit event loop
    def call_on_main(self, fn): ...             # thread-safe: schedule fn on main thread
    def call_later(self, ms, fn) -> handle: ... # timer
    def call_later_s(self, s, fn) -> handle: ...
    def cancel(self, handle): ...

    # Overlay
    def overlay_show(self): ...
    def overlay_hide(self): ...
    def overlay_set_status(self, status, level=0.0): ...
    def overlay_set_text(self, text): ...       # streaming transcript display
    def overlay_place(self): ...                # position on screen

    # Hotkey
    def hotkey_grab(self, spec, on_press, on_release): ...
    def hotkey_ungrab(self): ...
    def hotkey_replay_tap(self): ...

    # Clipboard / paste
    def paste_text(self, text, restore_ms): ... # clipboard inject
    def focused_is_terminal(self) -> bool: ...

    # Sound
    def play_sound(self, kind): ...             # "start", "done", "error"

    # Set-hotkey dialog
    def capture_hotkey(self, timeout_s) -> dict | None: ...
```

**State machine states:** idle, armed, listening, handsfree_stop, transcribing, cleaning

**Streaming transcription thread:**
- Starts when state enters "listening"
- Every `stream_interval_s` (default 1.0s), calls `recorder.get_snapshot()`
- Transcribes with `engine.transcribe(audio, fast=True)`
- Pushes text to overlay via `backend.call_on_main(backend.overlay_set_text, text)`
- For long audio (>30s), locks in "confirmed" prefix and only re-transcribes the tail (~10s rolling window)
- Stops when state leaves "listening"

**Hands-free mode:**
- If hotkey is a non-typing key (Pause, F8, etc): tap = toggle hands-free
- If hotkey is a typing key (backtick, letter): tap = type the character normally
- Hands-free has a max duration (default 180s), then auto-stops

**Tap vs hold detection:**
- on_press: arm (start recording)
- If held past tap_ms: commit (show overlay, start streaming)
- on_release before tap_ms: tap action (replay key or toggle hands-free)
- on_release after tap_ms: finish (stop recording, final transcribe, cleanup, paste)

### platform/linux.py

Port from mintflow_v0.py. Uses:
- **Xlib** for hotkey grab (X.GrabModeAsync, handles autorepeat debounce)
- **GTK3** for overlay window (custom Cairo drawing with waveform bars)
- **PangoCairo** for streaming text rendering in the overlay
- **xclip** for clipboard
- **xdotool** for paste injection (Ctrl+V or Ctrl+Shift+V for terminals)
- **paplay** for sounds
- **xprop** WM_CLASS for terminal detection

The overlay with streaming text:
- Default: 280x64 pill with waveform + "Listening..." label
- When text starts: expand to ~500x(120+), add wrapped text below waveform
- Text rendered with PangoCairo, word-wrapped, max width ~460px
- If text exceeds visible area, show only the last portion
- On cleanup/done: collapse back, hide after brief delay

GTK3 main loop integration:
- `GLib.idle_add` for call_on_main
- `GLib.timeout_add` for call_later
- `GLib.source_remove` for cancel

### platform/macos.py

Uses:
- **pynput** `keyboard.Listener` with suppress for hotkey (CGEventTap backend)
- **tkinter** for overlay window (Toplevel with overrideredirect, -topmost, -alpha)
- **Canvas** text item for streaming transcript
- **subprocess**: `pbcopy`/`pbpaste` for clipboard
- **subprocess**: `osascript -e 'tell application "System Events" to keystroke "v" using command down'` for paste
- **subprocess**: `osascript -e 'tell application "System Events" to get name of first process whose frontmost is true'` for terminal detection (check for Terminal.app, iTerm2, Alacritty, etc)
- **afplay** or NSSound for sounds (use system sounds if available)

tkinter main loop integration:
- `root.after(0, fn)` for call_on_main
- `root.after(ms, fn)` for call_later
- `root.after_cancel(id)` for cancel

The overlay should match the dark pill aesthetic of the Linux version: dark semi-transparent rounded rectangle, white text, waveform bars drawn on Canvas.

### platform/windows.py

Uses:
- **pynput** `keyboard.Listener` with suppress for hotkey (SetWindowsHookEx backend)
- **tkinter** for overlay window (same as macOS)
- **pyperclip** for clipboard
- **pynput** `keyboard.Controller` for paste (Ctrl+V simulation)
- **subprocess**: PowerShell command or win32gui for terminal detection (check for WindowsTerminal.exe, cmd.exe, powershell.exe, ConHost)
- **winsound** or system sounds for audio feedback

Same overlay design as macOS (tkinter Canvas).

### platform/__init__.py

```python
import sys

def get_backend():
    if sys.platform == "linux":
        from .linux import LinuxBackend
        return LinuxBackend()
    elif sys.platform == "darwin":
        from .macos import MacBackend
        return MacBackend()
    elif sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")
```

### cli.py

Entry point function `main()` handles:
- `mintflow` or `mintflow run` — start daemon
- `mintflow quit` or `mintflow stop` — stop daemon (send SIGTERM / taskkill)
- `mintflow setup` — first-run wizard: detect GPU, configure model, set hotkey, install Ollama model
- `mintflow set-hotkey` — capture hotkey dialog
- `mintflow test-mic` — record 2.5s, transcribe, print
- `mintflow test-inject` — paste test string
- `mintflow demo` — show overlay animation
- `mintflow models` — print GPU detection and recommended model
- `mintflow -h` / `mintflow --help`

PID file management for single-instance enforcement.
Signal handlers (SIGTERM, SIGINT) for clean shutdown.

---

## Overlay Visual Design (All Platforms)

### Listening (no text yet)
```
┌─────────────────────────────────────────────┐
│  Listening        ▎▌▊█▊▌▎▏ ▎▌▊█▊▌▎        │
└─────────────────────────────────────────────┘
```
280x64, dark pill (#121214 at 92% opacity), waveform bars (cyan #8AD8FF), positioned bottom-center of monitor, 72px from bottom edge.

### Listening (with streaming text)
```
┌─────────────────────────────────────────────────────┐
│  Listening        ▎▌▊█▊▌▎▏ ▎▌▊█▊▌▎                │
│                                                     │
│  Can you tell the team the launch is slipping to    │
│  Monday? We're still waiting on legal to sign off   │
│  on the new terms page...                           │
└─────────────────────────────────────────────────────┘
```
Expands to ~500x(120-180), text appears below waveform, white at 85% opacity, word-wrapped, last 3 lines visible.

### Cleaning up / Transcribing
```
┌─────────────────────────────────────────────┐
│  Cleaning up      ▎▌▊▌▎▏ ▎▌▊▌▎            │
└─────────────────────────────────────────────┘
```
280x64 pill, yellow bars (#FFD94D) for cleaning, white bars for transcribing.

### Done
Same pill, green bars (#8DEB9F), visible 380ms then hide.

### Error
Same pill, red bars (#FF7373), visible 900ms then hide.

**All overlays:** click-through (no input), always-on-top, skip taskbar/pager.

---

## README Requirements

The README must be readable by someone who has never used a terminal. Structure:

1. **Hero section** — one sentence: "Talk instead of typing. Works everywhere."
2. **How it works** — Hold key, speak, release. 15-word explanation.
3. **Install** — three separate sections for macOS, Windows, Linux. Each has numbered steps with copy-paste commands. No jargon without explanation.
4. **Set your hotkey** — `mintflow set-hotkey` (window pops, press your key)
5. **Settings** — table of config.json options with plain-English descriptions
6. **Custom vocabulary** — how to add names/terms to vocabulary.txt
7. **GPU & model info** — `mintflow models` shows what was detected
8. **Troubleshooting** — FAQ with common issues
9. **Privacy** — "Everything runs on your computer. Nothing leaves your machine."

---

## Install Script Requirements

Each script should:
1. Check Python 3.10+ (suggest install if missing)
2. Install system dependencies (portaudio, xclip, etc)
3. `pip install mintflow` (or `pip install .` for dev)
4. Check if Ollama is installed (suggest install if not)
5. Pull default Ollama model (`ollama pull qwen2.5:7b`)
6. Run `mintflow setup` (GPU detection + set-hotkey)
7. Set up autostart (systemd user service / launchd / Start Menu)
8. Print "Ready! Press [KEY] to talk."

### Linux (scripts/install-linux.sh)
```bash
# apt install python3-pip python3-gi python3-xlib xclip xdotool libportaudio2
# pip install mintflow
# ollama pull qwen2.5:7b
# mintflow setup
```

### macOS (scripts/install-mac.sh)
```bash
# brew install python portaudio ollama
# pip3 install mintflow
# ollama pull qwen2.5:7b
# mintflow setup
```

### Windows (scripts/install-win.ps1)
```powershell
# winget install Python.Python.3.12
# pip install mintflow
# winget install Ollama.Ollama
# ollama pull qwen2.5:7b
# mintflow setup
```

---

## GitHub Actions (release.yml)

On tag push (v*):
1. Build sdist + wheel (`python -m build`)
2. Create GitHub Release
3. Upload wheel + sdist as release assets
4. Include install instructions in release notes

No PyInstaller binaries for v1.0.0 (pip install is simpler).

---

## Critical Implementation Notes

1. **The rewrite prompt MUST use /api/chat, not /api/generate.** The system prompt prevents the LLM from treating transcript content as instructions. The v0 /api/generate approach caused the LLM to echo instructions back. This was the #1 bug.

2. **The sanity check (`_sane_rewrite`) MUST validate LLM output.** Check for refusal markers ("as an ai", "i cannot"), meta-output ("here is the", "rewritten text"), and length ratio (output should be 30%-200% of input word count). Bad output falls back to regex cleanup.

3. **Streaming uses `get_snapshot()` not `stop()`.** The recorder keeps running while the streaming thread reads copies of the audio buffer.

4. **beam_size=1 for streaming, beam_size=5 for final.** Streaming needs to be fast (~0.3s for 10s audio on GPU). Final transcription can take longer for quality.

5. **Terminal detection before rewrite, not after.** The terminal flag changes cleanup behavior (no trailing period, no forced capital, preserve command syntax like "grep -r").

6. **PID file in temp dir, not config dir.** Different path per platform.

7. **No `os.getuid()` on Windows.** Use `os.getpid()` or `os.getlogin()` for PID file naming.

8. **pynput suppress=True on Mac/Windows.** Without suppress, the hotkey event reaches the focused app AND mintflow. For typing keys this means double-input.

9. **The hotkey `keycode:N` format.** When set-hotkey captures a non-standard key, it stores the raw hardware keycode. The platform backend must handle this format.

10. **No em dashes in output.** Replace with commas. The v0 cleanup does this.

11. **Vocabulary feeds both Whisper and cleanup LLM.** Whisper gets it as initial_prompt glossary. Cleanup LLM gets it as "Names and terms the speaker often says (use these exact spellings)."
