"""Turn a narration script into per-clip audio plus a deterministic timeline.

Timeline rules (spec):
- hook starts at 0s
- clips are laid out sequentially with a 0.4s breathing gap between them
- each anchor segment "arrives" (playhead reaches its anchor_date) at
  start_s + duration_s - 0.3s, i.e. the voice line lands right as the
  playhead hits the anchor
- finale and cta are appended last
- total video duration = last clip end + 2.0s freeze frame
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stock_video_generator.errors import TTSUnavailableError
from stock_video_generator.scripting import NarrationScript
from stock_video_generator.tts.base import TTSProvider

GAP_S = 0.4
TAIL_S = 2.0
ANCHOR_LEAD_S = 0.3

ClipRole = Literal["hook", "segment", "finale", "cta"]


class AudioClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: ClipRole
    text: str
    anchor_date: date | None = None
    file: str
    start_s: float
    duration_s: float
    arrive_s: float | None = None


class AudioTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    simulation_id: str
    voice_id: str
    speed: float
    gap_s: float = GAP_S
    tail_s: float = TAIL_S
    anchor_lead_s: float = ANCHOR_LEAD_S
    segments: list[AudioClip]
    total_duration_s: float


def _round(value: float) -> float:
    return round(value + 1e-9, 3)


def build_timeline(
    simulation_id: str,
    voice_id: str,
    speed: float,
    clips: list[tuple[str, ClipRole, str, date | None, str, float]],
    *,
    gap_s: float = GAP_S,
    tail_s: float = TAIL_S,
    anchor_lead_s: float = ANCHOR_LEAD_S,
) -> AudioTimeline:
    """Pure, deterministic timeline layout from measured clip durations.

    clips: (id, role, text, anchor_date, filename, duration_s)
    """
    laid_out: list[AudioClip] = []
    cursor = 0.0
    for clip_id, role, text, anchor_date, filename, duration in clips:
        if duration <= 0:
            raise TTSUnavailableError(
                "配音音频时长无效。",
                detail=f"{filename} 实测时长 {duration}s",
            )
        arrive = None
        if role == "segment":
            arrive = _round(cursor + max(0.0, duration - anchor_lead_s))
        laid_out.append(
            AudioClip(
                id=clip_id,
                role=role,
                text=text,
                anchor_date=anchor_date,
                file=filename,
                start_s=_round(cursor),
                duration_s=_round(duration),
                arrive_s=arrive,
            )
        )
        cursor += duration + gap_s
    total = _round(laid_out[-1].start_s + laid_out[-1].duration_s + tail_s)
    return AudioTimeline(
        simulation_id=simulation_id,
        voice_id=voice_id,
        speed=speed,
        gap_s=gap_s,
        tail_s=tail_s,
        anchor_lead_s=anchor_lead_s,
        segments=laid_out,
        total_duration_s=total,
    )


def _mutagen_probe(path: Path) -> float:
    from mutagen.mp3 import MP3

    return float(MP3(str(path)).info.length)


def clip_plan(script: NarrationScript) -> list[tuple[str, ClipRole, str, date | None, str]]:
    """(id, role, text, anchor_date, filename) for every clip to synthesize."""
    plan: list[tuple[str, ClipRole, str, date | None, str]] = [
        ("hook", "hook", script.hook, None, "hook.mp3"),
    ]
    for position, segment in enumerate(script.segments):
        clip_id = f"segment_{position + 1:02d}"
        plan.append(
            (clip_id, "segment", segment.narration, segment.anchor_date, f"{clip_id}.mp3")
        )
    plan.append(("finale", "finale", script.finale, None, "finale.mp3"))
    plan.append(("cta", "cta", script.cta, None, "cta.mp3"))
    return plan


async def synthesize_narration(
    script: NarrationScript,
    simulation_id: str,
    audio_dir: Path,
    tts: TTSProvider,
    voice_id: str,
    speed: float,
    timeline_path: Path,
    *,
    probe_duration: Callable[[Path], float] = _mutagen_probe,
) -> AudioTimeline:
    """Synthesize every clip (skipping existing files) and write audio_timeline.json."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    measured: list[tuple[str, ClipRole, str, date | None, str, float]] = []
    for clip_id, role, text, anchor_date, filename in clip_plan(script):
        output_path = audio_dir / filename
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            await tts.synthesize(text, voice_id, speed, output_path)
        duration = probe_duration(output_path)
        measured.append((clip_id, role, text, anchor_date, filename, duration))
    timeline = build_timeline(
        simulation_id,
        voice_id,
        speed,
        measured,
    )
    temporary = timeline_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(timeline.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(timeline_path)
    return timeline


def load_timeline(path: Path) -> AudioTimeline:
    return AudioTimeline.model_validate_json(path.read_text(encoding="utf-8"))


def timeline_audio_missing(timeline: AudioTimeline, audio_dir: Path) -> list[str]:
    return [
        clip.file
        for clip in timeline.segments
        if not (audio_dir / clip.file).is_file() or (audio_dir / clip.file).stat().st_size <= 0
    ]
