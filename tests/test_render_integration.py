from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from stock_video_generator.config import PROJECT_ROOT, Settings


@pytest.mark.integration
@pytest.mark.render
def test_short_fixture_video_is_ffprobe_readable(tmp_path):
    """Renderer plumbing test only; fixture data is never used by production."""

    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    node = settings.resolve_node_executable()
    if not node:
        pytest.fail("未找到 Node.js；请设置 NODE_EXECUTABLE。")
    output = tmp_path / "fixture.mp4"
    command = [
        node,
        str(PROJECT_ROOT / "apps" / "renderer" / "scripts" / "render.mjs"),
        "--spec",
        str(PROJECT_ROOT / "tests" / "fixtures" / "visualization_spec.json"),
        "--output",
        str(output),
        "--concurrency",
        "1",
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT / "apps" / "renderer",
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    report_path = Path(f"{output}.validation.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert output.is_file() and output.stat().st_size > 0
    assert report["valid"]
    assert report["metadata"]["width"] == 1920
    assert report["metadata"]["height"] == 1080
    assert abs(report["metadata"]["fps"] - 30) < 0.01
    assert report["metadata"]["codec"] == "h264"
