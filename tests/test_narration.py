from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest
from stock_video_generator.errors import TTSUnavailableError
from stock_video_generator.models import ProviderHealth
from stock_video_generator.narration import (
    AudioTimeline,
    build_timeline,
    load_timeline,
    synthesize_narration,
    timeline_audio_missing,
)
from stock_video_generator.scripting import NarrationScript, ScriptSegment
from stock_video_generator.tts.base import TTSProvider, Voice
from stock_video_generator.visualization import build_narration_spec


def _script() -> NarrationScript:
    return NarrationScript(
        hook="2021年1月4日，你把100万全仓了贵州茅台。",
        segments=[
            ScriptSegment(
                anchor_date=date(2021, 6, 1),
                narration="6月冲到130.2万，赚了30.2%。",
                subtitle="2021.06 高点：+30.2%",
                emphasis="surge",
            ),
            ScriptSegment(
                anchor_date=date(2022, 10, 31),
                narration="10月最惨，只剩68.3万。",
                subtitle="2022.10 谷底：-47.5%",
                emphasis="crash",
            ),
            ScriptSegment(
                anchor_date=date(2024, 5, 6),
                narration="收复失地，重回110万。",
                subtitle="2024.05 收复：+10.0%",
                emphasis="recovery",
            ),
            ScriptSegment(
                anchor_date=date(2025, 7, 24),
                narration="故事的结局，定格在92万。",
                subtitle="结局：-8.0%",
                emphasis="crash",
            ),
        ],
        finale="拿到今天，100万只剩92万。",
        cta="如果是你，你会在哪一天卖掉？评论区聊聊。",
    )


class FakeTTS(TTSProvider):
    name = "fake"

    def __init__(self, durations: dict[str, float]) -> None:
        self.durations = durations
        self.calls: list[str] = []

    async def list_voices(self) -> list[Voice]:
        return [Voice(voice_id="fake", name="假音色", language="zh-CN")]

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float,
        output_path: Path,
    ) -> Path:
        self.calls.append(output_path.name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-mp3")
        return output_path

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name="tts", available=True, message="fake")


def test_build_timeline_layout() -> None:
    clips = [
        ("hook", "hook", "钩子", None, "hook.mp3", 2.0),
        ("segment_01", "segment", "第一段", date(2021, 6, 1), "segment_01.mp3", 3.0),
        ("segment_02", "segment", "第二段", date(2022, 10, 31), "segment_02.mp3", 2.5),
        ("finale", "finale", "结局", None, "finale.mp3", 2.0),
        ("cta", "cta", "互动", None, "cta.mp3", 1.5),
    ]
    timeline = build_timeline("sim-1", "voice", 1.08, clips)

    by_id = {clip.id: clip for clip in timeline.segments}
    assert by_id["hook"].start_s == 0.0
    # 段落间 0.4s 气口
    assert by_id["segment_01"].start_s == pytest.approx(2.4)
    assert by_id["segment_02"].start_s == pytest.approx(2.0 + 0.4 + 3.0 + 0.4)
    assert by_id["finale"].start_s == pytest.approx(2.4 + 3.0 + 0.4 + 2.5 + 0.4)
    # 锚点到达时间 = 段起点 + 时长 − 0.3s 修正
    assert by_id["segment_01"].arrive_s == pytest.approx(2.4 + 3.0 - 0.3)
    assert by_id["hook"].arrive_s is None
    assert by_id["finale"].arrive_s is None
    # 总时长 = 最后一段结束 + 2s 定格
    expected_total = 1.5 + 2.0
    last = timeline.segments[-1]
    assert timeline.total_duration_s == pytest.approx(last.start_s + last.duration_s + 2.0)
    assert timeline.total_duration_s == pytest.approx(
        2.0 + 0.4 + 3.0 + 0.4 + 2.5 + 0.4 + 2.0 + 0.4 + expected_total
    )


def test_build_timeline_rejects_zero_duration() -> None:
    clips = [("hook", "hook", "钩子", None, "hook.mp3", 0.0)]
    with pytest.raises(TTSUnavailableError):
        build_timeline("sim-1", "voice", 1.08, clips)


def test_synthesize_narration_writes_timeline_and_skips_existing(tmp_path: Path) -> None:
    durations = {
        "hook.mp3": 2.0,
        "segment_01.mp3": 3.0,
        "segment_02.mp3": 2.5,
        "segment_03.mp3": 2.2,
        "segment_04.mp3": 2.1,
        "finale.mp3": 1.8,
        "cta.mp3": 1.6,
    }
    tts = FakeTTS(durations)
    audio_dir = tmp_path / "audio"
    timeline_path = tmp_path / "audio_timeline.json"

    timeline = asyncio.run(
        synthesize_narration(
            _script(),
            "sim-1",
            audio_dir,
            tts,
            "zh-CN-XiaoxiaoNeural",
            1.08,
            timeline_path,
            probe_duration=lambda path: durations[path.name],
        )
    )

    assert len(timeline.segments) == 7  # hook + 4 段 + finale + cta
    assert timeline.segments[0].role == "hook"
    assert timeline.segments[-2].role == "finale"
    assert timeline.segments[-1].role == "cta"
    assert timeline_path.is_file()
    assert timeline_audio_missing(timeline, audio_dir) == []

    # 断点重跑：文件已存在时不重复合成
    calls_after_first_run = list(tts.calls)
    again = asyncio.run(
        synthesize_narration(
            _script(),
            "sim-1",
            audio_dir,
            tts,
            "zh-CN-XiaoxiaoNeural",
            1.08,
            timeline_path,
            probe_duration=lambda path: durations[path.name],
        )
    )
    assert sorted(calls_after_first_run) == sorted(durations.keys())  # 首轮每个片段各一次
    assert tts.calls == calls_after_first_run  # 第二轮零新增合成
    assert again.total_duration_s == timeline.total_duration_s

    loaded = load_timeline(timeline_path)
    assert loaded.model_dump_json() == timeline.model_dump_json()


def test_timeline_audio_missing_detects_gap(tmp_path: Path) -> None:
    timeline = AudioTimeline(
        simulation_id="sim-1",
        voice_id="v",
        speed=1.0,
        segments=[
            {
                "id": "hook",
                "role": "hook",
                "text": "t",
                "file": "hook.mp3",
                "start_s": 0.0,
                "duration_s": 1.0,
            }
        ],
        total_duration_s=3.0,
    )
    assert timeline_audio_missing(timeline, tmp_path) == ["hook.mp3"]


def test_build_narration_spec_maps_clips(tmp_path: Path) -> None:
    clips = [
        ("hook", "hook", "钩子", None, "hook.mp3", 2.0),
        ("segment_01", "segment", "第一段", date(2021, 6, 1), "segment_01.mp3", 3.0),
        ("finale", "finale", "结局", None, "finale.mp3", 2.0),
        ("cta", "cta", "互动", None, "cta.mp3", 1.5),
    ]
    timeline = build_timeline("sim-1", "voice", 1.08, clips)
    script = NarrationScript(
        hook="钩子",
        segments=[
            ScriptSegment(
                anchor_date=date(2021, 6, 1),
                narration="第一段",
                subtitle="2021.06 高点",
                emphasis="surge",
            )
        ],
        finale="结局",
        cta="互动",
    )
    spec = build_narration_spec(script, timeline, tmp_path)

    assert spec.hook_end_s == pytest.approx(2.0)
    assert spec.chart_end_s == pytest.approx(timeline.total_duration_s - 2.0)
    assert len(spec.segments) == 1
    assert spec.segments[0].arrive_s == pytest.approx(2.4 + 3.0 - 0.3)
    assert spec.segments[0].audio_id == "segment_01"
    assert {clip.role for clip in spec.audio} == {"hook", "segment", "finale", "cta"}
    hook = next(clip for clip in spec.audio if clip.id == "hook")
    assert hook.file == "narration/sim-1/hook.mp3"
    assert hook.source_path == str((tmp_path / "hook.mp3").resolve())
