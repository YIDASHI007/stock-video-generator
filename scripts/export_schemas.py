from __future__ import annotations

import json
from pathlib import Path

from stock_video_generator.models import SimulationRequest, SimulationResult
from stock_video_generator.visualization import VisualizationSpec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "packages" / "schemas"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    schemas = {
        "simulation-request.schema.json": SimulationRequest.model_json_schema(),
        "simulation.schema.json": SimulationResult.model_json_schema(),
        "visualization-spec.schema.json": VisualizationSpec.model_json_schema(),
    }
    for filename, schema in schemas.items():
        path = OUTPUT_DIR / filename
        path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(path)


if __name__ == "__main__":
    main()
