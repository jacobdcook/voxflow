"""Whisper transcription engine and CUDA library preload."""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback

import numpy as np

from mintflow.cleanup import local_cleanup, ollama_rewrite
from mintflow.config import JUNK_TRANSCRIPTS, load_config, load_vocabulary, log


def ensure_cuda_libs() -> None:
    """Load pip NVIDIA wheel .so/.dll files if system CUDA is not on the path."""
    import ctypes
    import ctypes.util
    import glob as _glob
    import site

    if ctypes.util.find_library("cublas") or ctypes.util.find_library("cublas64_12"):
        return

    roots: list[str] = [sys.prefix]
    try:
        roots.extend(site.getsitepackages())
        user_site = site.getusersitepackages()
        if user_site:
            roots.append(user_site)
    except Exception:
        pass

    patterns: list[str] = []
    for root in roots:
        patterns.append(os.path.join(root, "nvidia", "*", "lib"))
        patterns.append(os.path.join(root, "nvidia", "*", "bin"))
        patterns.append(os.path.join(root, "lib", "python*", "site-packages", "nvidia", "*", "lib"))
        patterns.append(os.path.join(root, "Lib", "site-packages", "nvidia", "*", "lib"))
        patterns.append(os.path.join(root, "Lib", "site-packages", "nvidia", "*", "bin"))

    loaded: set[str] = set()
    mode = getattr(ctypes, "RTLD_GLOBAL", 0)
    for pattern in patterns:
        for d in _glob.glob(pattern):
            sos = _glob.glob(os.path.join(d, "*.so*")) + _glob.glob(os.path.join(d, "*.dll"))
            for so in sorted(sos):
                if so in loaded:
                    continue
                loaded.add(so)
                try:
                    ctypes.CDLL(so, mode=mode)
                except OSError:
                    pass


_ensure_cuda_libs = ensure_cuda_libs
ensure_cuda_libs()


class Engine:
    def __init__(
        self,
        model_name: str | dict,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
        cfg: dict | None = None,
    ) -> None:
        if isinstance(model_name, dict):
            cfg = model_name
            model_name = str(cfg.get("model") or "small")
            device = str(cfg.get("device") or "cpu")
            compute_type = str(cfg.get("compute_type") or "int8")
            language = str(cfg.get("language") or "en")
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.cfg = dict(cfg) if cfg else load_config()
        self.cfg.setdefault("model", model_name)
        self.cfg.setdefault("device", device)
        self.cfg.setdefault("compute_type", compute_type)
        self.cfg.setdefault("language", language)
        self.model = None
        self.ready = threading.Event()
        self.error: str | None = None

    def preload(self) -> None:
        try:
            ensure_cuda_libs()
            from faster_whisper import WhisperModel

            t0 = time.monotonic()
            log(f"loading whisper {self.model_name} on {self.device}")
            self.model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            log(f"whisper ready in {time.monotonic() - t0:.1f}s")
            self._warmup_ollama()
            self.ready.set()
        except Exception:
            self.error = traceback.format_exc()
            log(f"preload failed:\n{self.error}")
            self.ready.set()

    def _warmup_ollama(self) -> None:
        if self.cfg.get("cleanup") != "ollama":
            return
        try:
            import httpx

            httpx.post(
                f"{self.cfg['ollama_url']}/api/chat",
                json={
                    "model": self.cfg["ollama_model"],
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"num_predict": 1, "temperature": 0},
                },
                timeout=20.0,
            )
            log("ollama warmup ok")
        except Exception as e:
            log(f"ollama warmup skipped: {e}")

    def transcribe(
        self,
        audio: np.ndarray,
        fast: bool = False,
        vocab_prompt: str = "",
    ) -> str:
        if self.model is None:
            raise RuntimeError(self.error or "whisper not loaded")
        if audio is None or getattr(audio, "size", 0) == 0:
            return ""
        prompt = "Dictation with punctuation."
        vocab = vocab_prompt or load_vocabulary()
        if vocab:
            prompt = f"Glossary: {vocab}. " + prompt
        kwargs: dict = {
            "language": self.language or None,
            "beam_size": 1 if fast else 5,
            "vad_filter": not fast,
            "condition_on_previous_text": False,
            "initial_prompt": prompt,
            "temperature": 0.0,
            "without_timestamps": True,
        }
        if not fast:
            kwargs["best_of"] = 5
            kwargs["vad_parameters"] = {"min_silence_duration_ms": 400}
        segments, _info = self.model.transcribe(audio, **kwargs)
        parts = []
        for s in segments:
            t = s.text.strip()
            if not t:
                continue
            if getattr(s, "no_speech_prob", 0.0) > 0.66 and getattr(s, "avg_logprob", 0.0) < -1.0:
                continue
            parts.append(t)
        raw = " ".join(parts).strip()
        if raw.lower().strip(" .!?") in JUNK_TRANSCRIPTS:
            return ""
        return raw

    def rewrite(self, text: str, terminal: bool = False) -> str:
        text = text.strip()
        if not text:
            return ""
        if self.cfg.get("cleanup") == "ollama":
            polished = ollama_rewrite(text, self.cfg, terminal)
            if polished:
                return polished
        return local_cleanup(text, terminal)
