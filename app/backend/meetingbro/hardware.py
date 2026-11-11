from __future__ import annotations
import os
import platform
from dataclasses import dataclass, field

@dataclass(frozen=True)
class HardwareProfile:
    cpu_count: int
    cuda_available: bool = False
    onnx_providers: tuple[str, ...] = field(default_factory=tuple)
    onnx_cuda_available: bool = False
    recommended_qwen_provider: str = "cpu"
    recommended_qwen_threads: int = 2
    recommended_runtime_profile: str = "balanced"
    apple_metal_available: bool = False

    @property
    def label(self):
        return f"{'cuda' if self.cuda_available else 'metal' if self.apple_metal_available else 'cpu'}/{self.cpu_count}c"

    @property
    def summary(self):
        return f"Qwen3-ASR / {self.label}"


def detect_hardware_profile() -> HardwareProfile:
    cpu_count = max(1, os.cpu_count() or 1)
    metal = platform.system() == "Darwin" and platform.machine() == "arm64"
    cuda = False
    if not metal:
        try:
            import torch
            cuda = torch.cuda.is_available()
        except ImportError:
            pass
    return HardwareProfile(cpu_count=cpu_count, cuda_available=cuda, apple_metal_available=metal,
        recommended_qwen_provider="cuda" if cuda else "cpu", recommended_qwen_threads=2 if cpu_count < 12 else 3,
        recommended_runtime_profile="performance" if cuda else "balanced" if metal or cpu_count > 4 else "summary_only")
