from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..schemas import OriginalLanguage


@dataclass
class ASRSegment:
    start_time: float
    end_time: float
    text: str
    language: OriginalLanguage
    confidence: float
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None
    compression_ratio: Optional[float] = None


class ASRAdapter(ABC):
    """Implementations may be local (faster-whisper) or remote (cloud ASR).

    ``forced_language`` carries the user's explicit language choice. When
    ``None`` the adapter must auto-detect. The system supports zh / en / de as
    first-class targets; other languages may be returned as ``"unknown"``.
    """

    def initialize(self) -> None:
        """Load reusable model resources when an adapter supports eager loading."""
        loader = getattr(self, "ensure_loaded", None)
        if loader is not None:
            loader()

    def reset_session(self) -> None:
        """Reset session-specific state without discarding a shared model."""

    def shutdown(self) -> None:
        """Release resources when the application, not a session, stops."""
        close = getattr(self, "close", None)
        if close is not None:
            close()

    @abstractmethod
    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int,
        *,
        forced_language: Optional[str] = None,
        offset_seconds: float = 0.0,
        initial_prompt: Optional[str] = None,
        quality_preset: str = "realtime",
    ) -> list[ASRSegment]:
        ...
