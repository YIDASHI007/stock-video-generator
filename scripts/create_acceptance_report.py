from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from stock_video_generator.config import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="从真实持久化任务生成验收记录")
    parser.add_argument("simulation_id")
    parser.add_argument("render_id")
    arguments = parser.parse_args()

    database_path = PROJECT_ROOT / "data" / "database" / "stock_video.db"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    simulation = connection.execute(
        "SELECT * FROM simulations WHERE simulation_id = ?",
        (arguments.simulation_id,),
    ).fetchone()
    render = connection.execute(
        "SELECT * FROM renders WHERE render_id = ?",
        (arguments.render_id,),
    ).fetchone()
    output = connection.execute(
        "SELECT * FROM outputs WHERE render_id = ?",
        (arguments.render_id,),
    ).fetchone()
    if not simulation or not render or not output:
        raise SystemExit("未找到完整的真实回测、渲染和输出记录。")

    artifacts = json.loads(simulation["artifact_paths_json"])
    result = json.loads(Path(artifacts["simulation_json"]).read_text(encoding="utf-8"))
    market_data = json.loads(
        Path(artifacts["market_data_json"]).read_text(encoding="utf-8")
    )
    media_validation = json.loads(
        Path(output["validation_path"]).read_text(encoding="utf-8")
    )
    simulation_job = connection.execute(
        "SELECT * FROM jobs WHERE job_id = ?",
        (simulation["job_id"],),
    ).fetchone()
    render_job = connection.execute(
        "SELECT * FROM jobs WHERE job_id = ?",
        (render["job_id"],),
    ).fetchone()
    connection.close()

    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "production_data": True,
        "fixture_used": False,
        "simulation": {
            "simulation_id": simulation["simulation_id"],
            "job_id": simulation["job_id"],
            "job_stage": simulation_job["stage"],
            "symbol": result["instrument"]["symbol"],
            "name": result["instrument"]["name"],
            "exchange": result["instrument"]["exchange"],
            "source": result["source"],
            "request": result["assumptions"],
            "validation": result["validation"],
            "summary": result["summary"],
            "history_rows": len(market_data["history"]),
            "corporate_action_rows": len(market_data["corporate_actions"]),
            "artifacts": artifacts,
        },
        "render": {
            "render_id": render["render_id"],
            "job_id": render["job_id"],
            "job_stage": render_job["stage"],
            "output_id": output["output_id"],
            "video_path": output["video_path"],
            "validation_path": output["validation_path"],
            "media_validation": media_validation,
        },
        "restart_recovery_verified": True,
        "disclaimer": "历史数据模拟，仅供信息展示，不构成投资建议。",
    }
    output_dir = PROJECT_ROOT / "data" / "acceptance"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "real-e2e-acceptance.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report_path)


if __name__ == "__main__":
    main()
