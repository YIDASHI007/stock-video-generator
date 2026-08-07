from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from stock_video_generator.models import ProviderHealth


class Voice(BaseModel):
    voice_id: str
    name: str
    language: str


class TTSProvider(ABC):
    """Provider boundary for optional narration without exposing market data."""

    name: str

    @abstractmethod
    async def list_voices(self) -> list[Voice]:
        raise NotImplementedError

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float,
        output_path: Path,
    ) -> Path:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        raise NotImplementedError
