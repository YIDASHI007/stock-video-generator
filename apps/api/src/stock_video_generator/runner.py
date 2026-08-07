from __future__ import annotations

from datetime import date
from uuid import uuid4

from stock_video_generator.artifacts import save_simulation_artifacts
from stock_video_generator.config import Settings
from stock_video_generator.errors import MarketDataValidationError
from stock_video_generator.market_data import MarketDataService
from stock_video_generator.models import SimulationRequest, SimulationResult
from stock_video_generator.simulation import simulate_buy_and_hold
from stock_video_generator.validation import validate_market_data
from stock_video_generator.visualization import (
    VisualizationSpec,
    build_visualization_spec,
)


class SimulationRunner:
    def __init__(self, settings: Settings, market_data: MarketDataService | None = None) -> None:
        self.settings = settings
        self.market_data = market_data or MarketDataService(settings)

    async def run(
        self,
        request: SimulationRequest,
    ) -> tuple[SimulationResult, VisualizationSpec, dict[str, str]]:
        simulation_id = str(uuid4())
        requested_end = date.today() if request.end_date == "latest" else request.end_date
        instrument, bars, actions, source = await self.market_data.fetch_bundle(
            request.symbol,
            request.buy_date,
            requested_end,
        )
        validation = validate_market_data(
            instrument,
            bars,
            actions,
            requested_start=request.buy_date,
            requested_end=requested_end,
            non_trading_day_policy=request.non_trading_day_policy,
        )
        if not validation.valid:
            raise MarketDataValidationError(
                "行情校验失败，已阻止回测和视频生成。",
                detail="；".join(validation.errors),
            )
        result = simulate_buy_and_hold(
            request=request,
            instrument=instrument,
            bars=bars,
            actions=actions,
            validation=validation,
            source=source,
            simulation_id=simulation_id,
        )
        visualization = build_visualization_spec(result)
        paths = save_simulation_artifacts(
            self.settings.data_dir / "simulations",
            request=request,
            result=result,
            visualization=visualization,
            bars=bars,
            actions=actions,
        )
        return result, visualization, paths
