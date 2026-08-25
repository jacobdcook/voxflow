# mintflow

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)](https://github.com/jacobdcook/mintflow)
[![Release](https://img.shields.io/github/v/release/jacobdcook/mintflow?include_prereleases)](https://github.com/jacobdcook/mintflow/releases)

Talk instead of typing. Works everywhere.

Local voice-to-text for Linux, macOS, and Windows. No account, no cloud, no word cap.

## How it works

Hold a key, speak, and release. Cleaned text pastes into whatever app you are in.

A small overlay appears while you talk. When you let go, mintflow turns speech into clean text and pastes it into the window that has focus: email, chat, a browser, a terminal, anywhere.

Tap a non-typing key (Pause, F8, and similar) to start hands-free mode. Tap again to stop. Hold any hotkey to talk the usual way.

## Install

You need Python 3.10 or newer, a microphone, and (for the best cleanup) [Ollama](https://ollama.com) running on the same computer. The installers below check these for you.

The first run downloads a Whisper speech model. Expect a few minutes and one to three gigabytes of disk, depending on which model your hardware gets.

### macOS

1. Open **Terminal** (Spotlight, then type `Terminal`).
2. If you do not have [Homebrew](https://brew.sh) yet (the usual way to install tools on a Mac), paste this and press Return:

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

3. Download mintflow and run the installer:

   ```bash
   git clone https://github.com/jacobdcook/mintflow.git
   cd mintflow
   bash scripts/install-mac.sh
   ```

4. When macOS asks, allow **Microphone** and **Accessibility** for Terminal (or for the Python app). mintflow needs both: one to hear you, one to paste text.

5. A window will ask you to press your hotkey. Press the key you want to hold while talking, then you are done.

Already have Python 3.10+ and Homebrew? You can install by hand. Homebrew's Python
refuses plain `pip install`, so put mintflow in its own small environment:

```bash
brew install python python-tk portaudio ollama
python3 -m venv ~/.local/share/mintflow/venv
~/.local/share/mintflow/venv/bin/pip install "mintflow[desktop]"
ln -sf ~/.local/share/mintflow/venv/bin/mintflow ~/.local/bin/mintflow
ollama pull qwen2.5:7b
mintflow setup
```

### Windows

1. Open **PowerShell**. Click Start, type `PowerShell`, press Enter.
2. Download mintflow and run the installer:

   ```powershell
   git clone https://github.com/jacobdcook/mintflow.git
   cd mintflow
   powershell -ExecutionPolicy Bypass -File .\scripts\install-win.ps1
   ```

   If `git` is missing, install it from [git-scm.com](https://git-scm.com/download/win), then open a new PowerShell window and try again.

3. If Windows asks about the microphone, choose **Allow**.
4. A window will ask you to press your hotkey. Press the key you want to hold while talking.

Already have Python 3.10+? You can install by hand:

```powershell
pip install "mintflow[desktop]"
ollama pull qwen2.5:7b
mintflow setup
```

Install [Ollama for Windows](https://ollama.com/download) first if the `ollama` command is not found.

### Linux

Works on Ubuntu, Debian, Linux Mint, and other apt-based desktops. The overlay and hotkey currently need **X11** (Cinnamon, GNOME on Xorg, XFCE, and similar). Wayland is not supported yet.

1. Open **Terminal**.
2. Download mintflow and run the installer:

   ```bash
   git clone https://github.com/jacobdcook/mintflow.git
   cd mintflow
   bash scripts/install-linux.sh
   ```

   The script may ask for your password so it can install packages (`xclip`, `xdotool`, PortAudio, GTK).
3. A window will ask you to press your hotkey. Press the key you want to hold while talking.

Already have Python 3.10+? You can install by hand. Recent Ubuntu, Debian, and Mint
block plain `pip install` into the system Python, so use a small environment of its
own (`--system-site-packages` is what lets it see the GTK and Xlib packages apt just
installed):

```bash
sudo apt install python3-pip python3-venv python3-gi python3-gi-cairo python3-xlib xclip xdotool libportaudio2
python3 -m venv --system-site-packages ~/.local/share/mintflow/venv
~/.local/share/mintflow/venv/bin/pip install "mintflow[linux]"
ln -sf ~/.local/share/mintflow/venv/bin/mintflow ~/.local/bin/mintflow
ollama pull qwen2.5:7b
mintflow setup
```

Install [Ollama](https://ollama.com/download) if you want LLM cleanup (recommended).

After install, start it any time with:

```bash
mintflow
```

Stop it with:

```bash
mintflow quit
```

## Set your hotkey

The default hotkey is **Pause**. That key is easy to hold and rarely types a character.

To pick a different key:

```bash
mintflow set-hotkey
```

A small window appears. Press the key (or key combination) you want. mintflow saves it and uses it from then on.

- **Hold** the key: record, then paste.
- **Tap** a non-typing key (Pause, F8, F9, ...): hands-free on/off.
- **Tap** a typing key (letter, backtick): type that character as usual.

## Settings

Settings live in a file named `config.json`. mintflow creates it on first run.

| Computer | File |
| --- | --- |
| Linux | `~/.config/mintflow/config.json` |
| macOS | `~/Library/Application Support/mintflow/config.json` |
| Windows | `%APPDATA%\mintflow\config.json` |

Open that file in any text editor, change a value, save, then run `mintflow quit` and `mintflow` again.

| Setting | Default | What it means |
| --- | --- | --- |
| `hotkey` | `pause` | The key you hold to talk. Prefer `mintflow set-hotkey` over editing this by hand. |
| `handsfree_max_s` | `180` | Longest hands-free recording, in seconds (3 minutes). |
| `max_seconds` | `600` | Hard stop for any recording, in seconds (10 minutes). Protects you if a key sticks. |
| `model` | `auto` | Whisper model size. `auto` picks one that fits your computer. |
| `device` | `auto` | Where Whisper runs: `cpu` or `cuda`. `auto` detects a GPU. |
| `compute_type` | `auto` | Internal number format. Leave on `auto` unless you know you need `float16` or `int8`. |
| `language` | `en` | Spoken language (`en`, `es`, `fr`, ...). |
| `style` | `casual` | How cleaned text should sound: `formal`, `casual`, or `very-casual`. |
| `cleanup` | `ollama` | `ollama` rewrites with a local model. `local` uses simple cleanup only. |
| `ollama_model` | `qwen2.5:7b` | Which Ollama model cleans up speech. |
| `ollama_url` | `http://127.0.0.1:11434` | Where Ollama is listening. Leave this unless you changed Ollama. |
| `sample_rate` | `16000` | Microphone sample rate. Leave this unless you have a reason to change it. |
| `tap_ms` | `220` | How long a press must last to count as a hold, not a tap. |
| `repeat_ms` | `80` | Ignores keyboard repeat while you hold the key. |
| `min_seconds` | `0.35` | Ignore recordings shorter than this (seconds). |
| `sounds` | `true` | Play a short sound when recording starts and finishes. |
| `sound_volume` | `0.3` | How loud those sounds are, from `0.0` to `1.0`. Windows plays them at the system volume. |
| `restore_clipboard_ms` | `450` | After paste, put your old clipboard contents back. |
| `stream_interval_s` | `1.0` | How often the overlay updates the words you are saying. On a slow machine mintflow backs off on its own. |

## Custom vocabulary

Names, product titles, and jargon often come out wrong. Add the spellings you care about to `vocabulary.txt` in the same folder as `config.json` (see the table above).

One term per line. Lines that start with `#` are ignored.

```text
# People and products I say often
Mintflow
Jacob Cook
Qwen
Ollama
CySA+
```

mintflow feeds this list to Whisper and to the cleanup model so those spellings are preferred. After you edit the file, run `mintflow quit` and start mintflow again.

## GPU & model

mintflow looks at your hardware and picks a Whisper model. See what it chose:

```bash
mintflow models
```

Typical picks:

| Hardware | Whisper model | Device |
| --- | --- | --- |
| NVIDIA GPU with 6 GB+ VRAM | `large-v3` | CUDA |
| NVIDIA GPU with 3 to 6 GB VRAM | `medium` | CUDA |
| NVIDIA GPU with less than 3 GB VRAM | `small` | CUDA |
| Apple Silicon | `large-v3` | CPU (int8) |
| AMD (ROCm) | `medium` | CPU (int8) |
| CPU with 16 GB+ RAM | `medium` | CPU |
| CPU with 8 to 16 GB RAM | `small` | CPU |
| CPU with less than 8 GB RAM | `base` | CPU |

To force a size, set `"model"` in `config.json` to `base`, `small`, `medium`, or `large-v3`, then restart mintflow.

The first time a model is used, faster-whisper downloads it. That is local after that. Cleanup uses Ollama (`qwen2.5:7b` by default), which also stays on your machine.

## Privacy

Everything runs on your computer. Nothing leaves your machine.

Speech is transcribed with Whisper on your CPU or GPU. Cleanup talks to Ollama on `127.0.0.1`. There is no mintflow account, no cloud API key, and no telemetry. If the network is unplugged, dictation still works (Ollama must already be installed and the Whisper model already downloaded).

The only way a transcript leaves your computer is if you point `ollama_url` at another machine yourself. mintflow writes a warning to `mintflow.log` when you do.

## Troubleshooting

**The overlay never appears.** Make sure mintflow is running (`mintflow`). Hold the hotkey for longer than a quick tap. On macOS, grant Accessibility. On Linux, use an X11 session, not Wayland.

**The key does nothing.** Run `mintflow set-hotkey` and press the key again. Another program may already own that key. Pause and F8 are usually free. On macOS, Accessibility must be allowed or the key never reaches mintflow.

**I spoke, but nothing pasted.** Click into a text field first. Try `mintflow test-inject` and see if the test sentence appears. On Linux, install `xclip` and `xdotool`. On macOS, allow Accessibility. Some apps block paste; click the field and try again.

**The microphone is silent or too quiet.** Run `mintflow test-mic`, speak a sentence, and read what it prints. It tells you whether the problem is the device, the volume, or the language setting. Check the OS microphone privacy toggle and the default input device, and close other apps that might be holding the mic.

**"mintflow heard silence."** The recording came through empty. Your computer is recording from a different device than the one you spoke into, or that device is muted. Pick the right input in your sound settings, then confirm with `mintflow test-mic`.

**"mintflow is still finishing the last recording."** You pressed the hotkey again while the previous one was still being transcribed. Wait for the overlay to disappear, then talk again.

**Names and jargon come out wrong.** Add them to `vocabulary.txt` (see [Custom vocabulary](#custom-vocabulary)).

**Cleanup is messy, or the model answers the question you dictated.** Confirm Ollama is running (`ollama list`). Pull the model: `ollama pull qwen2.5:7b`. Set `"cleanup": "local"` in `config.json` if you want regex-only cleanup with no LLM.

**First run is slow.** Whisper is downloading a model. Later runs are much faster. GPU machines finish transcription in a fraction of the time CPU machines need.

**"mintflow is already running".** That is normal if it started at login. Use `mintflow quit` to stop it, then `mintflow` to start again.

**Linux: I am on Wayland.** Switch the session to Xorg/X11 at the login screen, or use a desktop that still offers X11 (Cinnamon, XFCE, MATE). The Linux hotkey grab is X11-only in this version.

**Windows: `mintflow` is not recognized.** Close PowerShell, open a new window, and try again so PATH updates. Or call it with `python -m mintflow`.

**macOS: tkinter error about the overlay.** Install Tk: `brew install python-tk`, then reinstall mintflow.

More commands:

```bash
mintflow help          # list commands
mintflow test-mic      # record 2.5 seconds and print the transcript
mintflow test-inject   # paste a test sentence into the focused window
mintflow demo          # show the overlay animation
mintflow models        # print GPU detection and the chosen Whisper model
```

Logs are written next to your config file (`mintflow.log`).

## Contributing

Bug reports and pull requests are welcome. Please open an issue first for large changes.

- Keep mintflow local-only. Do not add cloud STT or cloud LLM backends.
- Read [PLAN.md](PLAN.md) for architecture, module layout, and overlay behavior.
- Match the existing style. No AI co-author tags in commits.

```bash
git clone https://github.com/jacobdcook/mintflow.git
cd mintflow
pip install -e ".[linux]"    # Linux
pip install -e ".[desktop]"  # macOS or Windows
```

Run the checks before you open a pull request. They need no display, microphone, or
network, and they cover the hold/tap state machine, config loading, clipboard safety,
packaging, and this README:

```bash
python3 tests/run_all.py
```

Then test the path you touched for real: `mintflow models`, `mintflow test-mic`, and
one actual hold-to-talk paste on your OS.

## License

[MIT](LICENSE). Copyright (c) 2026 Jacob Cook.

You can use, copy, modify, and share mintflow. There is no warranty.
