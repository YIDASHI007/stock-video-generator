from stock_video_generator.tts.base import TTSProvider, Voice
from stock_video_generator.tts.disabled import DisabledTTSProvider
from stock_video_generator.tts.edge_tts_provider import EdgeTTSProvider

__all__ = [
    "DisabledTTSProvider",
    "EdgeTTSProvider",
    "TTSProvider",
    "Voice",
    "create_tts_provider",
]


def create_tts_provider(settings) -> TTSProvider:
    """Factory honouring TTS_PROVIDER (edge | disabled). Defaults to edge."""
    if str(settings.tts_provider).strip().lower() in {"disabled", "off", "none"}:
        return DisabledTTSProvider()
    return EdgeTTSProvider()
