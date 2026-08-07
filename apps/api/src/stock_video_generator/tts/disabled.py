from __future__ import annotations

from pathlib import Path

from stock_video_generator.errors import DependencyUnavailableError
from stock_video_generator.models import ProviderHealth
from stock_video_generator.tts.base import TTSProvider, Voice


class DisabledTTSProvider(TTSProvider):
    name = "disabled"

    async def list_voices(self) -> list[Voice]:
        return []

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float,
        output_path: Path,
    ) -> Path:
        raise DependencyUnavailableError("未配置 TTS Provider；请关闭配音后生成视频。")

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            name="tts",
            available=False,
            message="MVP 未配置 TTS；无配音视频仍可正常生成。",
        )
