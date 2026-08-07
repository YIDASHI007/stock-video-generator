"""选题模块离线测试：评分纯函数、买入日顺延、队列补充与冷却。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    PipelineRunRecord,
    PipelineStatus,
    StoryCandidateRecord,
    StoryCandidateStatus,
    TopicRecord,
    TopicStatus,
    now_utc,
)
from stock_video_generator.errors import UniverseUnavailableError
from stock_video_generator.models import (
    CorporateAction,
    CorporateActionType,
    HistoryBar,
    Market,
)
from stock_video_generator.pipeline import PipelinePolicy
from stock_video_generator.topics import (
    ANGLE_COMPOUND,
    ANGLE_CRASH,
    ANGLE_ROLLERCOASTER,
    ANGLE_SURGE,
    DEFAULT_ANGLE_WEIGHTS,
    LongHorizonStory,
    TopicDirective,
    TopicSelector,
    UniverseEntry,
    adjust_bars_for_splits,
    choose_angle,
    compute_drama_metrics,
    load_universe,
    pick_buy_date,
    story_data_quality,
)

FETCHED_AT = datetime(2025, 1, 1, tzinfo=UTC)


def make_bars(points: list[tuple[date, float]]) -> list[HistoryBar]:
    return [
        HistoryBar(
            date=day,
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=1000,
            currency="USD",
            source="fixture",
            fetched_at=FETCHED_AT,
        )
        for day, price in points
    ]


def dense_bars(points: list[tuple[date, float]]) -> list[HistoryBar]:
    """Expand sparse shape fixtures into daily history for story quality checks."""
    ordered = sorted(points)
    dense: list[tuple[date, float]] = []
    for (start, start_price), (end, end_price) in zip(ordered, ordered[1:], strict=False):
        days = max(1, (end - start).days)
        for offset in range(days):
            ratio = offset / days
            dense.append(
                (start + timedelta(days=offset), start_price + (end_price - start_price) * ratio)
            )
    dense.append(ordered[-1])
    return make_bars(dense)


def weekly_dates(start: date, count: int) -> list[date]:
    return [start + timedelta(weeks=index) for index in range(count)]


# 四类典型形态
def surge_points() -> list[tuple[date, float]]:
    # 横盘多年 → 3 个月暴涨 4 倍 → 高位稳住
    points = [(day, 10.0) for day in weekly_dates(date(2018, 1, 1), 200)]
    for index, price in enumerate([10, 15, 22, 30, 40]):
        points.append((date(2022, 1, 3) + timedelta(weeks=index * 3), price))
    points.extend((day, 40.0) for day in weekly_dates(date(2022, 4, 1), 150))
    return points


def crash_points() -> list[tuple[date, float]]:
    # 缓慢阴跌后崩掉，终点仍在地板
    points = [(day, 10.0) for day in weekly_dates(date(2018, 1, 1), 150)]
    points.append((date(2021, 1, 4), 11.0))
    for index, price in enumerate([9, 7, 5, 3, 2.1]):
        points.append((date(2021, 2, 1) + timedelta(weeks=index * 8), price))
    points.extend((day, 2.1) for day in weekly_dates(date(2022, 1, 3), 150))
    return points


def rollercoaster_points() -> list[tuple[date, float]]:
    # 一年內暴涨 4 倍 → 回撤 70% → 明显收复
    points = [(day, 10.0) for day in weekly_dates(date(2018, 1, 1), 200)]
    for index, price in enumerate([15, 25, 40]):
        points.append((date(2022, 1, 3) + timedelta(weeks=index * 10), price))
    for index, price in enumerate([30, 20, 12]):
        points.append((date(2022, 4, 1) + timedelta(weeks=index * 10), price))
    for index, price in enumerate([14, 17, 20]):
        points.append((date(2022, 8, 1) + timedelta(weeks=index * 10), price))
    points.extend((day, 20.0) for day in weekly_dates(date(2023, 1, 2), 100))
    return points


def compound_points() -> list[tuple[date, float]]:
    # 十年慢牛：每年约 +22%，最大回撤 10%
    points: list[tuple[date, float]] = []
    price = 10.0
    for index, day in enumerate(weekly_dates(date(2015, 1, 5), 520)):
        if index == 300:
            price *= 0.90  # 一次 10% 回撤
        points.append((day, price))
        price *= 1.004  # 周涨约 0.4% ≈ 年化 22%
    return points


def test_compute_drama_metrics_runup_and_drawdown():
    bars = make_bars(
        [
            (date(2020, 1, 6), 10.0),
            (date(2020, 1, 13), 5.0),
            (date(2020, 1, 20), 20.0),
            (date(2020, 1, 27), 18.0),
        ]
    )
    metrics = compute_drama_metrics(bars)
    assert metrics.max_gain == pytest.approx(3.0)
    assert metrics.runup_trough_date == date(2020, 1, 13)
    assert metrics.runup_peak_date == date(2020, 1, 20)
    assert metrics.runup_days == 7
    assert metrics.max_drawdown == pytest.approx(0.5)
    assert metrics.drawdown_peak_date == date(2020, 1, 6)
    assert metrics.drawdown_trough_date == date(2020, 1, 13)
    assert metrics.total_return == pytest.approx(0.8)
    assert metrics.final_drawdown == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("points_fn", "expected_angle"),
    [
        (surge_points, ANGLE_SURGE),
        (crash_points, ANGLE_CRASH),
        (rollercoaster_points, ANGLE_ROLLERCOASTER),
        (compound_points, ANGLE_COMPOUND),
    ],
)
def test_angle_classification_four_archetypes(points_fn, expected_angle):
    metrics = compute_drama_metrics(make_bars(points_fn()))
    angle, score = choose_angle(metrics, DEFAULT_ANGLE_WEIGHTS)
    assert angle == expected_angle
    assert score > 0


def test_angle_hint_forces_classification():
    metrics = compute_drama_metrics(make_bars(surge_points()))
    angle, _ = choose_angle(metrics, DEFAULT_ANGLE_WEIGHTS, angle_hint=ANGLE_COMPOUND)
    assert angle == ANGLE_COMPOUND


def test_buy_date_is_real_trading_day_for_each_angle():
    for points_fn in [
        surge_points,
        crash_points,
        rollercoaster_points,
        compound_points,
    ]:
        bars = make_bars(points_fn())
        buy_date = pick_buy_date(bars)
        assert buy_date in {bar.date for bar in bars}


def test_buy_date_does_not_change_with_story_angle():
    bars = make_bars(surge_points())
    assert pick_buy_date(bars) == date(2018, 1, 1)


def test_buy_date_uses_first_available_trading_day():
    bars = make_bars(
        [
            (date(2020, 1, 6), 10.0),
            (date(2021, 6, 14), 12.0),
            (date(2021, 6, 21), 13.0),
            (date(2024, 6, 17), 20.0),
        ]
    )
    assert pick_buy_date(bars) == date(2020, 1, 6)


def test_split_adjustment_removes_mechanical_price_drop():
    bars = make_bars(
        [
            (date(2024, 6, 7), 1000.0),
            (date(2024, 6, 10), 100.0),
            (date(2024, 6, 11), 110.0),
        ]
    )
    actions = [
        CorporateAction(
            ex_date=date(2024, 6, 10),
            event_type=CorporateActionType.SPLIT,
            split_ratio=10.0,
            currency="USD",
            source="fixture",
        )
    ]
    adjusted = adjust_bars_for_splits(bars, actions)
    assert [bar.close for bar in adjusted] == pytest.approx([100.0, 100.0, 110.0])
    assert compute_drama_metrics(adjusted).max_drawdown == pytest.approx(0.0)


def test_crypto_quality_accepts_short_history_but_stock_rule_does_not():
    bars = dense_bars(
        [
            (date(2025, 1, 1), 10.0),
            (date(2025, 7, 1), 30.0),
        ]
    )

    _, stock_issues = story_data_quality(bars)
    crypto_score, crypto_issues = story_data_quality(bars, min_hold_years=0.25)

    assert stock_issues
    assert crypto_issues == []
    assert crypto_score > 0


def test_load_universe_missing_file_fails_honestly(tmp_path: Path):
    with pytest.raises(UniverseUnavailableError):
        load_universe(tmp_path / "universe.json")


def test_load_universe_rejects_invalid_entries(tmp_path: Path):
    path = tmp_path / "universe.json"
    path.write_text(
        json.dumps([{"symbol": "X", "name": "X", "market": "CN", "angle_hint": "nope"}]),
        encoding="utf-8",
    )
    with pytest.raises(UniverseUnavailableError):
        load_universe(path)


class FakeMarketData:
    """离线行情桩：providers/get_history 接口与 MarketDataService 一致。"""

    def __init__(
        self,
        bars_by_symbol: dict[str, list[HistoryBar]],
        actions_by_symbol: dict[str, list[CorporateAction]] | None = None,
    ) -> None:
        self.bars_by_symbol = bars_by_symbol
        self.actions_by_symbol = actions_by_symbol or {}
        self.providers = {"fake": object()}

    @staticmethod
    def provider_name_for_symbol(symbol: str) -> str:
        return "fake"

    async def get_history(self, provider, symbol, start_date, end_date):
        return self.bars_by_symbol[symbol], {"fetched_at": FETCHED_AT.isoformat()}

    async def get_actions(self, provider, symbol, start_date, end_date):
        return self.actions_by_symbol.get(symbol, []), {
            "fetched_at": FETCHED_AT.isoformat()
        }


def build_selector(
    tmp_path: Path,
    universe: list[dict[str, object]],
    bars_by_symbol: dict[str, list[HistoryBar]],
) -> tuple[TopicSelector, Database]:
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    (settings.data_dir / "universe.json").write_text(
        json.dumps(universe, ensure_ascii=False),
        encoding="utf-8",
    )
    database = Database(settings)
    database.initialize()
    return (
        TopicSelector(settings, database, FakeMarketData(bars_by_symbol)),
        database,
    )


UNIVERSE = [
    {"symbol": "AAA", "name": "甲公司", "market": "CN"},
    {"symbol": "BBB", "name": "乙公司", "market": "CN"},
    {"symbol": "CCC", "name": "丙公司", "market": "US"},
]


def test_replenish_fills_pool_and_respects_markets_and_cooldown(tmp_path: Path):
    bars = {symbol: dense_bars(compound_points()) for symbol in ("AAA", "BBB", "CCC")}
    selector, database = build_selector(tmp_path, UNIVERSE, bars)
    # BBB 近 90 天内被消费过 → 冷却期内不得再进队
    with database.session() as session:
        session.add(
            TopicRecord(
                topic_id="consumed-1",
                symbol="BBB",
                name="乙公司",
                market="CN",
                buy_date="2022-01-03",
                amount=1_000_000,
                angle=ANGLE_COMPOUND,
                drama_score=0.1,
                status=TopicStatus.CONSUMED,
                consumed_at=now_utc() - timedelta(days=30),
            )
        )

    policy = PipelinePolicy(pool_target=2, markets=[Market.CN])
    report = asyncio.run(selector.replenish(policy, as_of=bars["AAA"][-1].date))

    # 只有 AAA 可入队：CCC 市场未启用、BBB 冷却中
    assert report["pool_size"] == 1
    added_symbols = {item["symbol"] for item in report["added"]}
    assert added_symbols == {"AAA"}
    assert any(item["symbol"] == "BBB" for item in report["skipped"])
    assert any(item["symbol"] == "CCC" for item in report["skipped"])

    with database.session() as session:
        topics = session.query(TopicRecord).all()
        assert len(topics) == 2
        queued = [topic for topic in topics if topic.status == TopicStatus.QUEUED]
        assert len(queued) == 1
        assert queued[0].symbol == "AAA"
        stored_buy_date = date.fromisoformat(queued[0].buy_date)
        assert stored_buy_date in {bar.date for bar in bars["AAA"]}


def test_replenish_allows_a_new_story_for_a_previously_produced_symbol(tmp_path: Path):
    bars = {symbol: dense_bars(compound_points()) for symbol in ("AAA", "BBB")}
    selector, database = build_selector(tmp_path, UNIVERSE[:2], bars)
    with database.session() as session:
        session.add(
            TopicRecord(
                topic_id="produced-topic",
                symbol="BBB",
                name="乙公司",
                market="CN",
                buy_date="2022-01-03",
                amount=1_000_000,
                angle=ANGLE_COMPOUND,
                drama_score=0.1,
                status=TopicStatus.CONSUMED,
                consumed_at=now_utc() - timedelta(days=180),
            )
        )
        session.add(
            PipelineRunRecord(
                run_id="produced-run",
                topic_id="produced-topic",
                status=PipelineStatus.COMPLETED,
                current_stage=PipelineStatus.COMPLETED,
            )
        )

    report = asyncio.run(
        selector.replenish(
            PipelinePolicy(pool_target=2, markets=[Market.CN]),
            as_of=bars["AAA"][-1].date,
        )
    )

    assert {item["symbol"] for item in report["added"]} == {"AAA", "BBB"}
    bbb = next(item for item in report["added"] if item["symbol"] == "BBB")
    assert bbb["buy_date"] != "2022-01-03"


def test_next_topic_rejects_preexisting_queued_duplicate(tmp_path: Path):
    selector, database = build_selector(tmp_path, UNIVERSE[:2], {})
    with database.session() as session:
        session.add_all(
            [
                TopicRecord(
                    topic_id="produced-topic",
                    symbol="BBB",
                    name="乙公司",
                    market="CN",
                    buy_date="2022-01-03",
                    amount=1_000_000,
                    angle=ANGLE_COMPOUND,
                    drama_score=1.0,
                    status=TopicStatus.CONSUMED,
                    consumed_at=now_utc() - timedelta(days=180),
                ),
                TopicRecord(
                    topic_id="queued-duplicate",
                    symbol="BBB",
                    name="乙公司",
                    market="CN",
                    buy_date="2022-01-03",
                    amount=1_000_000,
                    angle=ANGLE_COMPOUND,
                    drama_score=1.0,
                    status=TopicStatus.QUEUED,
                ),
                TopicRecord(
                    topic_id="queued-new",
                    symbol="AAA",
                    name="甲公司",
                    market="CN",
                    buy_date="2022-01-03",
                    amount=1_000_000,
                    angle=ANGLE_COMPOUND,
                    drama_score=1.0,
                    status=TopicStatus.QUEUED,
                ),
            ]
        )
        session.add(
            PipelineRunRecord(
                run_id="produced-run",
                topic_id="produced-topic",
                status=PipelineStatus.COMPLETED,
                current_stage=PipelineStatus.COMPLETED,
            )
        )

    selected = selector.next_topic()

    assert selected.topic_id == "queued-new"
    with database.session() as session:
        duplicate = session.get(TopicRecord, "queued-duplicate")
        assert duplicate.status == TopicStatus.REJECTED


def test_next_topic_and_pool_count_respect_enabled_markets(tmp_path: Path):
    selector, database = build_selector(tmp_path, UNIVERSE[:2], {})
    with database.session() as session:
        session.add_all(
            [
                TopicRecord(
                    topic_id="cn-topic",
                    symbol="AAA",
                    name="甲公司",
                    market="CN",
                    buy_date="2022-01-03",
                    amount=1_000_000,
                    angle=ANGLE_COMPOUND,
                    drama_score=1.0,
                    status=TopicStatus.QUEUED,
                    created_at=now_utc() - timedelta(minutes=2),
                ),
                TopicRecord(
                    topic_id="us-topic",
                    symbol="US-AAA",
                    name="US Test",
                    market="US",
                    buy_date="2022-01-03",
                    amount=1_000_000,
                    angle=ANGLE_COMPOUND,
                    drama_score=1.0,
                    status=TopicStatus.QUEUED,
                    created_at=now_utc() - timedelta(minutes=1),
                ),
            ]
        )

    assert selector.queued_count([Market.US]) == 1
    assert selector.queued_count([Market.CN, Market.US]) == 2
    assert selector.next_topic([Market.US]).topic_id == "us-topic"
    assert selector.next_topic([Market.CN]).topic_id == "cn-topic"


def test_next_topic_rejects_old_queue_items_that_do_not_match_directive(
    tmp_path: Path,
):
    selector, database = build_selector(tmp_path, UNIVERSE[:1], {})
    with database.session() as session:
        session.add_all(
            [
                TopicRecord(
                    topic_id="old-surge-topic",
                    symbol="AAA",
                    name="旧暴涨题",
                    market="CN",
                    buy_date="2018-01-02",
                    amount=1_000_000,
                    angle=ANGLE_SURGE,
                    drama_score=99,
                    status=TopicStatus.QUEUED,
                    created_at=now_utc() - timedelta(minutes=2),
                ),
                TopicRecord(
                    topic_id="matching-crash-topic",
                    symbol="BBB",
                    name="暴跌题",
                    market="CN",
                    buy_date="2019-01-02",
                    amount=1_000_000,
                    angle=ANGLE_CRASH,
                    drama_score=98,
                    status=TopicStatus.QUEUED,
                    created_at=now_utc() - timedelta(minutes=1),
                ),
                StoryCandidateRecord(
                    story_id="old-surge-story",
                    story_key="AAA|2018-01-02|surge",
                    symbol="AAA",
                    name="旧暴涨题",
                    market="CN",
                    buy_date="2018-01-02",
                    end_date="2026-01-02",
                    story_type="listing_start",
                    angle=ANGLE_SURGE,
                    hold_years=8,
                    start_price=1,
                    end_price=10,
                    forward_return_pct=900,
                    max_drawdown_pct=20,
                    quality_score=90,
                    content_score=99,
                    status=StoryCandidateStatus.QUEUED,
                    topic_id="old-surge-topic",
                ),
                StoryCandidateRecord(
                    story_id="matching-crash-story",
                    story_key="BBB|2019-01-02|crash",
                    symbol="BBB",
                    name="暴跌题",
                    market="CN",
                    buy_date="2019-01-02",
                    end_date="2026-01-02",
                    story_type="listing_start",
                    angle=ANGLE_CRASH,
                    hold_years=7,
                    start_price=100,
                    end_price=5,
                    forward_return_pct=-95,
                    max_drawdown_pct=97,
                    quality_score=90,
                    content_score=98,
                    status=StoryCandidateStatus.QUEUED,
                    topic_id="matching-crash-topic",
                ),
            ]
        )

    selected = selector.next_topic(
        [Market.CN],
        TopicDirective(crash_max_pct=-90),
    )

    assert selected is not None
    assert selected.topic_id == "matching-crash-topic"
    with database.session() as session:
        old_topic = session.get(TopicRecord, "old-surge-topic")
        old_story = session.get(StoryCandidateRecord, "old-surge-story")
        assert old_topic.status == TopicStatus.REJECTED
        assert old_story.status == StoryCandidateStatus.READY
        assert old_story.topic_id is None


def test_replenish_is_idempotent_when_pool_full(tmp_path: Path):
    bars = {"AAA": dense_bars(compound_points())}
    selector, _ = build_selector(tmp_path, UNIVERSE[:1], bars)
    policy = PipelinePolicy(pool_target=1, markets=[Market.CN])
    first = asyncio.run(selector.replenish(policy, as_of=bars["AAA"][-1].date))
    assert len(first["added"]) == 1
    # 队列已有 AAA（queued 也在冷却集合内），第二次补充不得重复入队
    second = asyncio.run(selector.replenish(policy, as_of=bars["AAA"][-1].date))
    assert second["added"] == []
    assert second["pool_size"] == 1


def test_score_bars_accepts_recent_listing_and_uses_listing_start(tmp_path: Path):
    selector, _ = build_selector(tmp_path, UNIVERSE[:1], {})
    entry = UniverseEntry(symbol="AAA", name="甲公司", market=Market.CN)
    short_bars = make_bars(
        [
            (date(2024, 1, 1) + timedelta(weeks=index), 10.0 + index * 0.2)
            for index in range(60)
        ]
    )
    policy = PipelinePolicy()
    topic = selector.score_bars(
        entry,
        short_bars,
        policy.angle_weights,
        date(2025, 1, 1),
    )
    assert topic is not None
    assert topic.buy_date == short_bars[0].date


# ---------- F3 选题偏好（TopicDirective）----------

from stock_video_generator.topics import (  # noqa: E402
    TopicDirective,
    directive_weights,
    passes_directive,
)


def test_passes_directive_empty_directive_always_passes():
    assert passes_directive(ANGLE_SURGE, 5.0, None)
    assert passes_directive(ANGLE_CRASH, -99.0, TopicDirective())


def test_passes_directive_surge_threshold():
    directive = TopicDirective(surge_min_pct=100.0)
    assert passes_directive(ANGLE_COMPOUND, 150.0, directive)
    assert not passes_directive(ANGLE_COMPOUND, 50.0, directive)


def test_passes_directive_crash_threshold():
    directive = TopicDirective(crash_max_pct=-80.0)
    assert passes_directive(ANGLE_CRASH, -90.0, directive)
    assert not passes_directive(ANGLE_CRASH, -50.0, directive)


def test_passes_directive_dual_thresholds_are_or():
    directive = TopicDirective(surge_min_pct=100.0, crash_max_pct=-80.0)
    assert passes_directive(ANGLE_SURGE, 500.0, directive)
    assert passes_directive(ANGLE_CRASH, -95.0, directive)
    assert not passes_directive(ANGLE_COMPOUND, 30.0, directive)


def test_passes_directive_prefer_angles_filters_angle():
    directive = TopicDirective(prefer_angles=[ANGLE_CRASH])
    assert not passes_directive(ANGLE_SURGE, 500.0, directive)
    assert passes_directive(ANGLE_CRASH, -10.0, directive)


def test_directive_weights_zeroes_unpreferred_angles():
    weights = directive_weights(
        TopicDirective(prefer_angles=[ANGLE_SURGE, ANGLE_CRASH]),
        dict(DEFAULT_ANGLE_WEIGHTS),
    )
    assert weights[ANGLE_SURGE] > 0 and weights[ANGLE_CRASH] > 0
    assert weights[ANGLE_ROLLERCOASTER] == 0
    assert weights[ANGLE_COMPOUND] == 0
    # 空偏好 → 原样返回
    assert directive_weights(TopicDirective(), dict(DEFAULT_ANGLE_WEIGHTS)) == dict(
        DEFAULT_ANGLE_WEIGHTS
    )


def test_topic_directive_validates_and_normalizes():
    with pytest.raises(ValueError):
        TopicDirective(prefer_angles=["not-an-angle"])
    directive = TopicDirective(prefer_symbols=[" ffie ", "FFIE", "aapl"])
    assert directive.prefer_symbols == ["FFIE", "AAPL"]


def test_score_bars_forward_return_filter(tmp_path: Path):
    selector, _ = build_selector(tmp_path, UNIVERSE[:1], {})
    entry = UniverseEntry(symbol="AAA", name="甲公司", market=Market.CN)
    surge_bars = make_bars(surge_points())  # 买入日 10 元 → 终点 40 元 ≈ +300%
    as_of = surge_bars[-1].date
    policy = PipelinePolicy()

    topic = selector.score_bars(entry, surge_bars, policy.angle_weights, as_of)
    assert topic is not None
    assert topic.forward_return_pct > 200

    # 要求暴涨 ≥500%：+300% 不满足 → 被过滤
    assert (
        selector.score_bars(
            entry,
            surge_bars,
            policy.angle_weights,
            as_of,
            directive=TopicDirective(surge_min_pct=500.0),
        )
        is None
    )
    # 要求暴跌 ≤-80%：+300% 不满足 → 被过滤
    assert (
        selector.score_bars(
            entry,
            surge_bars,
            policy.angle_weights,
            as_of,
            directive=TopicDirective(crash_max_pct=-80.0),
        )
        is None
    )
    # 双阈值“或”：满足暴涨一侧即通过
    assert (
        selector.score_bars(
            entry,
            surge_bars,
            policy.angle_weights,
            as_of,
            directive=TopicDirective(surge_min_pct=100.0, crash_max_pct=-80.0),
        )
        is not None
    )


def test_score_bars_prefer_angles_excludes_other_angles(tmp_path: Path):
    selector, _ = build_selector(tmp_path, UNIVERSE[:1], {})
    entry = UniverseEntry(symbol="AAA", name="甲公司", market=Market.CN)
    crash_bars = make_bars(crash_points())
    as_of = crash_bars[-1].date
    weights = directive_weights(
        TopicDirective(prefer_angles=[ANGLE_CRASH]), dict(DEFAULT_ANGLE_WEIGHTS)
    )
    topic = selector.score_bars(
        entry,
        crash_bars,
        weights,
        as_of,
        directive=TopicDirective(prefer_angles=[ANGLE_CRASH]),
    )
    assert topic is not None
    assert topic.angle == ANGLE_CRASH
    assert topic.forward_return_pct < 0


def test_replenish_respects_directive(tmp_path: Path):
    # AAA 慢牛（+22% 年化）、CCC 暴跌：偏好暴跌时只有 CCC 能入队
    bars = {
        "AAA": dense_bars(compound_points()),
        "CCC": dense_bars(crash_points()),
    }
    universe = [
        {"symbol": "AAA", "name": "甲公司", "market": "CN"},
        {"symbol": "CCC", "name": "丙公司", "market": "CN"},
    ]
    selector, _ = build_selector(tmp_path, universe, bars)
    policy = PipelinePolicy(
        pool_target=1,
        markets=[Market.CN],
        topic_directive=TopicDirective(crash_max_pct=-50.0),
    )
    report = asyncio.run(
        selector.replenish(
            policy,
            as_of=bars["CCC"][-1].date,
        )
    )
    added_symbols = {item["symbol"] for item in report["added"]}
    assert added_symbols == {"CCC"}
    assert any(
        item["symbol"] == "AAA" and "不满足选题偏好" in item["reason"]
        for item in report["skipped"]
    )


def test_replenish_prioritizes_preferred_symbols(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    bars = {
        "AAA": dense_bars(compound_points()),
        "BBB": dense_bars(compound_points()),
    }
    universe = [
        {"symbol": "AAA", "name": "甲公司", "market": "CN"},
        {"symbol": "BBB", "name": "乙公司", "market": "CN"},
    ]
    selector, _ = build_selector(tmp_path, universe, bars)

    def fake_story_score(entry, *_args, **_kwargs):
        score = 1.0 if entry.symbol == "AAA" else 100.0
        return [
            LongHorizonStory(
                entry=entry,
                buy_date=date(2021, 1, 4),
                end_date=date(2026, 1, 4),
                story_type="horizon_5y",
                angle=ANGLE_COMPOUND,
                hold_years=5.0,
                start_price=10.0,
                end_price=20.0,
                forward_return_pct=100.0,
                max_drawdown_pct=10.0,
                quality_score=100.0,
                content_score=score,
            )
        ]

    monkeypatch.setattr(selector, "score_story_candidates", fake_story_score)
    policy = PipelinePolicy(
        pool_target=1,
        markets=[Market.CN],
        topic_directive=TopicDirective(prefer_symbols=["AAA"]),
    )

    report = asyncio.run(selector.replenish(policy))

    assert [item["symbol"] for item in report["added"]] == ["AAA"]


def test_preview_counts_matches_without_writing(tmp_path: Path):
    bars = {
        "AAA": make_bars(compound_points()),
        "BBB": make_bars(crash_points()),
    }
    universe = [
        {"symbol": "AAA", "name": "甲公司", "market": "CN"},
        {"symbol": "BBB", "name": "乙公司", "market": "CN"},
    ]
    selector, database = build_selector(tmp_path, universe, bars)
    result = asyncio.run(
        selector.preview(TopicDirective(crash_max_pct=-50.0), [Market.CN])
    )
    assert result["count"] == 1
    assert result["matched"][0]["symbol"] == "BBB"
    assert result["matched"][0]["forward_return_pct"] < -50
    # 预览不写库
    assert selector.queued_count() == 0
    with database.session() as session:
        assert session.query(TopicRecord).count() == 0


def test_preview_uses_saved_angle_weights(tmp_path: Path):
    bars = {"AAA": make_bars(surge_points())}
    selector, _ = build_selector(tmp_path, UNIVERSE[:1], bars)
    result = asyncio.run(
        selector.preview(
            TopicDirective(),
            [Market.CN],
            {
                ANGLE_SURGE: 0,
                ANGLE_CRASH: 0,
                ANGLE_ROLLERCOASTER: 0,
                ANGLE_COMPOUND: 100,
            },
        )
    )

    assert result["count"] == 1
    assert result["matched"][0]["angle"] == ANGLE_COMPOUND
