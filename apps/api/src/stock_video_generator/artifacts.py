from __future__ import annotations

import csv
import json
from pathlib import Path

from pydantic import BaseModel

from stock_video_generator.models import (
    CorporateAction,
    HistoryBar,
    SimulationRequest,
    SimulationResult,
)
from stock_video_generator.visualization import VisualizationSpec


def _write_json(path: Path, value: BaseModel | dict[str, object]) -> None:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def save_simulation_artifacts(
    root: Path,
    *,
    request: SimulationRequest,
    result: SimulationResult,
    visualization: VisualizationSpec | None,
    bars: list[HistoryBar],
    actions: list[CorporateAction],
) -> dict[str, str]:
    output_dir = root / result.simulation_id
    output_dir.mkdir(parents=True, exist_ok=True)

    request_path = output_dir / "request.json"
    simulation_path = output_dir / "simulation.json"
    csv_path = output_dir / "simulation.csv"
    visualization_path = output_dir / "visualization_spec.json"
    source_path = output_dir / "source.json"
    validation_path = output_dir / "validation.json"
    market_data_path = output_dir / "market_data.json"

    _write_json(request_path, request)
    _write_json(simulation_path, result)
    if visualization is not None:
        _write_json(visualization_path, visualization)
    _write_json(source_path, result.source)
    _write_json(validation_path, result.validation)
    _write_json(
        market_data_path,
        {
            "schema_version": "1.0",
            "source": result.source.model_dump(mode="json"),
            "history": [bar.model_dump(mode="json") for bar in bars],
            "corporate_actions": [action.model_dump(mode="json") for action in actions],
        },
    )

    temporary_csv = csv_path.with_suffix(".csv.tmp")
    with temporary_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date",
                "close",
                "shares",
                "cash",
                "portfolio_value",
                "total_return_pct",
                "drawdown_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(point.model_dump(mode="json") for point in result.series)
    temporary_csv.replace(csv_path)

    return {
        "directory": str(output_dir.resolve()),
        "request_json": str(request_path.resolve()),
        "simulation_json": str(simulation_path.resolve()),
        "simulation_csv": str(csv_path.resolve()),
        "visualization_spec_json": str(visualization_path.resolve()),
        "source_json": str(source_path.resolve()),
        "validation_json": str(validation_path.resolve()),
        "market_data_json": str(market_data_path.resolve()),
    }


def write_visualization_spec(output_dir: Path, visualization: VisualizationSpec) -> Path:
    path = output_dir / "visualization_spec.json"
    _write_json(path, visualization)
    return path
