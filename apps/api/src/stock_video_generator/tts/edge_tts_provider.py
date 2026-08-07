"""edge-tts backed TTS provider (free, no API key, requires network access)."""

from __future__ import annotations

import time
from pathlib import Path

from stock_video_generator.errors import DependencyUnavailableError, TTSUnavailableError
from stock_video_generator.models import ProviderHealth
from stock_video_generator.tts.base import TTSProvider, Voice

ZH_CN_VOICES = [
    Voice(voice_id="zh-CN-XiaoxiaoNeural", name="晓晓（女声，默认）", language="zh-CN"),
    Voice(voice_id="zh-CN-YunxiNeural", name="云希（男声）", language="zh-CN"),
    Voice(voice_id="zh-CN-XiaoyiNeural", name="晓伊（女声）", language="zh-CN"),
]


def _edge_tts_module():
    try:
        import edge_tts
    except ImportError as exc:
        raise DependencyUnavailableError(
            "未安装 edge-tts。请在虚拟环境中执行 pip install edge-tts 后重启服务。"
        ) from exc
    return edge_tts


class EdgeTTSProvider(TTSProvider):
    name = "edge-tts"

    def __init__(self, timeout_seconds: float = 45) -> None:
        self.timeout_seconds = timeout_seconds

    async def list_voices(self) -> list[Voice]:
        _edge_tts_module()
        return list(ZH_CN_VOICES)

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float,
        output_path: Path,
    ) -> Path:
        edge_tts = _edge_tts_module()
        rate = f"{round((speed - 1) * 100):+d}%"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            communicate = edge_tts.Communicate(text, voice_id, rate=rate)
            await communicate.save(str(output_path))
        except DependencyUnavailableError:
            raise
        except Exception as exc:
            raise TTSUnavailableError(
                "edge-tts 语音合成失败（需要联网）。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise TTSUnavailableError(
                "edge-tts 未产出有效音频文件。",
                detail=f"输出文件为空：{output_path}",
            )
        return output_path

    async def health_check(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            edge_tts = _edge_tts_module()
        except DependencyUnavailableError as exc:
            return ProviderHealth(
                name="tts",
                available=False,
                message=str(exc),
            )
        try:
            communicate = edge_tts.Communicate("探测", "zh-CN-XiaoxiaoNeural")
            received = False
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    received = True
                    break
            if not received:
                raise RuntimeError("edge-tts 流式探测未返回音频数据")
        except Exception as exc:
            return ProviderHealth(
                name="tts",
                available=False,
                latency_ms=(time.monotonic() - started) * 1000,
                message=f"edge-tts 网络探测失败：{type(exc).__name__}: {exc}",
            )
        return ProviderHealth(
            name="tts",
            available=True,
            latency_ms=(time.monotonic() - started) * 1000,
            message="edge-tts 可用（微软在线语音，免费无需密钥，需要联网）。",
        )
