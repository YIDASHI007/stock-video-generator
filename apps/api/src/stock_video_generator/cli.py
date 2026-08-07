from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from stock_video_generator.config import REMOTION_COMPOSITION_ID, settings
from stock_video_generator.models import SimulationRequest
from stock_video_generator.runner import SimulationRunner


async def _run(request_path: Path, render: bool) -> None:
    request = SimulationRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    result, _, paths = await SimulationRunner(settings).run(request)
    output: dict[str, object] = {
        "simulation_id": result.simulation_id,
        "summary": result.summary.model_dump(mode="json"),
        "artifacts": paths,
    }
    if render:
        node = settings.resolve_node_executable()
        if not node:
            raise SystemExit("未找到 Node.js；请设置 NODE_EXECUTABLE。")
        video_path = (settings.data_dir / "outputs" / f"{result.simulation_id}.mp4").resolve()
        subprocess.run(
            [
                node,
                str(settings.runtime_dir / "apps" / "renderer" / "scripts" / "render.mjs"),
                "--spec",
                paths["visualization_spec_json"],
                "--output",
                str(video_path),
                "--composition",
                REMOTION_COMPOSITION_ID,
                "--concurrency",
                str(settings.render_max_concurrency),
            ],
            cwd=settings.runtime_dir / "apps" / "renderer",
            check=True,
        )
        output["video"] = str(video_path)
        output["validation_report"] = f"{video_path}.validation.json"
    print(json.dumps(output, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="执行真实股票历史回测")
    parser.add_argument("request", type=Path, help="SimulationRequest JSON 文件")
    parser.add_argument("--render", action="store_true", help="回测完成后渲染 MP4")
    arguments = parser.parse_args()
    asyncio.run(_run(arguments.request.resolve(), arguments.render))
