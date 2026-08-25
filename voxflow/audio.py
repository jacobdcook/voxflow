"""Cross-platform microphone recorder with a thread-safe live snapshot."""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

from voxflow.config import log


class Recorder:
    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self.level = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._stream is not None:
            self.stop()

        def cb(indata, _frames, _time_info, status):
            if status:
                log(f"audio status: {status}")
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
            with self._lock:
                self._chunks.append(mono)
                rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-9)
                self.level = min(1.0, rms * 18)

        with self._lock:
            self._chunks = []
            self.level = 0.0
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=1024,
                callback=cb,
            )
        self._stream.start()

    def get_snapshot(self) -> np.ndarray:
        """Return all recorded audio so far without stopping the stream.

        The streaming preview calls this once a second, so the per-block list is
        collapsed to a single array first: a ten-minute recording is otherwise
        ~9000 arrays to join on every call. Callers must treat the result as
        read-only; nothing here mutates it in place.
        """
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            if len(self._chunks) > 1:
                self._chunks = [np.concatenate(self._chunks)]
            return self._chunks[0]

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log(f"stream stop: {e}")
            self._stream = None
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._chunks)
            self._chunks = []
            self.level = 0.0
            return audio
