# Changelog

All notable changes to mintflow are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-08-25

### Added
- Hold-to-talk dictation: hold a hotkey, speak, release, cleaned text pastes into the focused app
- Hands-free mode: tap a non-typing hotkey to start, tap again to stop (3 minute safety cap)
- Live streaming preview: words appear in the overlay in real time while you speak
- Local speech-to-text with faster-whisper (large-v3 down to base, picked per hardware)
- LLM cleanup pass via Ollama (fillers removed, self-corrections applied, grammar fixed)
- Regex fallback cleanup when Ollama is unavailable
- GPU auto-detection: NVIDIA VRAM tiers, Apple Silicon, AMD ROCm, CPU RAM tiers
- `mintflow setup` first-run wizard (model pick + hotkey capture)
- `mintflow set-hotkey`: press any key, it becomes the hotkey (raw keycode support)
- Custom vocabulary file feeding both Whisper and the cleanup model
- Terminal-aware output: no trailing period, command syntax preserved in terminals
- Linux backend: X11 hotkey grab, GTK3 overlay, xclip/xdotool paste
- macOS backend: pynput hotkey, tkinter overlay, pbcopy/osascript paste
- Windows backend: pynput hotkey, tkinter overlay, pyperclip paste
- Overlay with waveform visualization and status colors
- Per-OS install scripts and step-by-step README for non-programmers
- GitHub Actions release workflow building bundled zips for Linux, macOS, and Windows
