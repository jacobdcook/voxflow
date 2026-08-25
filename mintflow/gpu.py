"""Detect GPU vendor/VRAM and recommend Whisper model, device, and compute type."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mintflow.config import log, save_config


@dataclass
class GPUInfo:
    vendor: str  # nvidia, apple, amd, cpu
    name: str
    vram_gb: float
    ram_gb: float


@dataclass
class ModelConfig:
    model: str
    device: str
    compute_type: str


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _ram_gb() -> float:
    if sys.platform == "linux":
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
        except (OSError, ValueError, IndexError):
            pass

    if sys.platform == "darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        if out:
            try:
                return int(out) / (1024 ** 3)
            except ValueError:
                pass

    if sys.platform == "win32":
        out = _run(["wmic", "computersystem", "get", "TotalPhysicalMemory", "/value"])
        for line in out.splitlines():
            if line.lower().startswith("totalphysicalmemory="):
                try:
                    return int(line.split("=", 1)[1].strip()) / (1024 ** 3)
                except ValueError:
                    pass

    return 8.0


def detect_gpu() -> GPUInfo:
    ram = _ram_gb()

    if shutil.which("nvidia-smi"):
        out = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
        best_name = ""
        best_vram = 0.0
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                vram = float(parts[-1]) / 1024.0
            except ValueError:
                continue
            name = ",".join(parts[:-1]).strip()
            if vram >= best_vram:
                best_vram = vram
                best_name = name or "NVIDIA GPU"
        if best_name:
            return GPUInfo(vendor="nvidia", name=best_name, vram_gb=best_vram, ram_gb=ram)

    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return GPUInfo(vendor="apple", name="Apple Silicon", vram_gb=ram, ram_gb=ram)

    if shutil.which("rocm-smi"):
        name = "AMD ROCm GPU"
        product = _run(["rocm-smi", "--showproductname"])
        for line in product.splitlines():
            lower = line.lower()
            if "card series" in lower or "card model" in lower or "gpu" in lower:
                name = line.strip()
        vram = 0.0
        mem = _run(["rocm-smi", "--showmeminfo", "vram"])
        for token in mem.replace(",", " ").split():
            try:
                value = float(token)
            except ValueError:
                continue
            if value > 256:
                vram = max(vram, value / 1024.0)
        return GPUInfo(vendor="amd", name=name, vram_gb=vram, ram_gb=ram)

    return GPUInfo(vendor="cpu", name="CPU", vram_gb=0.0, ram_gb=ram)


def recommend_model(gpu: GPUInfo) -> ModelConfig:
    if gpu.vendor == "nvidia":
        if gpu.vram_gb >= 6:
            return ModelConfig(model="large-v3", device="cuda", compute_type="float16")
        if gpu.vram_gb >= 3:
            return ModelConfig(model="medium", device="cuda", compute_type="float16")
        return ModelConfig(model="small", device="cuda", compute_type="float16")
    if gpu.vendor == "apple":
        return ModelConfig(model="large-v3", device="cpu", compute_type="int8")
    if gpu.vendor == "amd":
        return ModelConfig(model="medium", device="cpu", compute_type="int8")
    if gpu.ram_gb >= 16:
        return ModelConfig(model="medium", device="cpu", compute_type="int8")
    if gpu.ram_gb >= 8:
        return ModelConfig(model="small", device="cpu", compute_type="int8")
    return ModelConfig(model="base", device="cpu", compute_type="int8")


def setup_auto_config(cfg: dict) -> dict:
    keys = ("model", "device", "compute_type")
    if not any(str(cfg.get(k, "auto")).lower() == "auto" for k in keys):
        return cfg

    gpu = detect_gpu()
    rec = recommend_model(gpu)
    if str(cfg.get("model", "auto")).lower() == "auto":
        cfg["model"] = rec.model
    if str(cfg.get("device", "auto")).lower() == "auto":
        cfg["device"] = rec.device
    if str(cfg.get("compute_type", "auto")).lower() == "auto":
        cfg["compute_type"] = rec.compute_type

    save_config(cfg)
    chosen = (
        f"Detected {gpu.name} ({gpu.vendor}, "
        f"{gpu.vram_gb:.1f} GB VRAM, {gpu.ram_gb:.1f} GB RAM) -> "
        f"model={cfg['model']} device={cfg['device']} compute_type={cfg['compute_type']}"
    )
    log(chosen)
    return cfg
