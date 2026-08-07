"""成片缩略图：渲染完成后用 Remotion 内置 ffmpeg 截帧生成封面图。

缩略图统一存放在 ``data/outputs/{render_id}.jpg``。
截帧失败只记日志，绝不影响渲染任务本身的成功状态。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Literal

from stock_video_generator.config import Settings

logger = logging.getLogger(__name__)

# 截帧时间点（秒）：跳过片头，取第 3 秒画面作为封面。
CAPTURE_SECOND = 3
CoverVariant = Literal["portrait", "landscape"]


def find_ffmpeg(settings: Settings) -> Path | None:
    """在 pnpm 安装目录里定位 Remotion 自带的 ffmpeg（版本号不写死）。"""
    candidates = sorted(
        candidate
        for pnpm_dir in settings.pnpm_store_dirs
        for candidate in pnpm_dir.glob(
            "@remotion+compositor-*/node_modules/@remotion/compositor-*/ffmpeg.exe"
        )
    )
    return candidates[0] if candidates else None


def thumbnail_path(settings: Settings, render_id: str) -> Path:
    return (settings.data_dir / "outputs" / f"{render_id}.jpg").resolve()


def cover_path(
    settings: Settings,
    render_id: str,
    variant: CoverVariant,
) -> Path:
    """Return the deterministic Remotion cover path for a render."""
    return (
        settings.data_dir / "outputs" / f"{render_id}.cover-{variant}.png"
    ).resolve()


def capture_thumbnail(
    settings: Settings,
    video_path: str | Path,
    render_id: str,
) -> Path | None:
    """从成片截一帧保存为 jpg；任何失败都只记日志并返回 None。"""
    source = Path(video_path)
    if not source.is_file():
        logger.warning("缩略图截帧跳过：视频文件不存在 %s", source)
        return None
    ffmpeg = find_ffmpeg(settings)
    if ffmpeg is None:
        logger.warning("缩略图截帧跳过：未找到 Remotion 内置 ffmpeg。")
        return None
    target = thumbnail_path(settings, render_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                str(ffmpeg),
                "-y",
                "-ss",
                str(CAPTURE_SECOND),
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(target),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except Exception as exc:
        logger.warning("缩略图截帧失败 %s -> %s：%s", source, target, exc)
        return None
    if target.is_file() and target.stat().st_size > 0:
        return target
    logger.warning("缩略图截帧产物为空：%s", target)
    return None


def ensure_thumbnail(
    settings: Settings,
    render_id: str,
    video_path: str | Path,
) -> Path | None:
    """Prefer the generated landscape cover; fall back to the legacy video frame."""
    landscape_cover = cover_path(settings, render_id, "landscape")
    if landscape_cover.is_file() and landscape_cover.stat().st_size > 0:
        return landscape_cover
    existing = thumbnail_path(settings, render_id)
    if existing.is_file() and existing.stat().st_size > 0:
        return existing
    return capture_thumbnail(settings, video_path, render_id)
