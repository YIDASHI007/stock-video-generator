from __future__ import annotations

from stock_video_generator.config import Settings
from stock_video_generator.thumbnails import find_ffmpeg, find_ffprobe


def test_find_media_binaries_in_portable_remotion_layout(tmp_path):
    settings = Settings(
        runtime_dir=tmp_path,
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
    )
    compositor = (
        tmp_path
        / "apps"
        / "renderer"
        / "node_modules"
        / "@remotion"
        / "compositor-win32-x64-msvc"
    )
    compositor.mkdir(parents=True)
    ffmpeg = compositor / "ffmpeg.exe"
    ffprobe = compositor / "ffprobe.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")

    assert find_ffmpeg(settings) == ffmpeg.resolve()
    assert find_ffprobe(settings) == ffprobe.resolve()
