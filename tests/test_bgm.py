"""F4 背景音乐：策略字段、渲染前 spec 注入、端点行为（离线）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from stock_video_generator.config import Settings
from stock_video_generator.jobs import JobManager
from stock_video_generator.pipeline import PipelinePolicy


def _make_context(tmp_path: Path) -> tuple[Settings, Path]:
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    spec_path = settings.data_dir / "spec.json"
    spec_path.write_text(
        json.dumps({"simulation_id": "sim-1", "narration": None}),
        encoding="utf-8",
    )
    return settings, spec_path


def _inject(settings: Settings, spec_path: Path) -> Path:
    # _inject_bgm 只依赖 self.settings，无需构造完整 JobManager。
    return JobManager._inject_bgm(SimpleNamespace(settings=settings), spec_path)


def test_policy_bgm_file_defaults_to_none():
    assert PipelinePolicy().bgm_file is None


def test_inject_bgm_no_policy_returns_original(tmp_path: Path):
    settings, spec_path = _make_context(tmp_path)
    assert _inject(settings, spec_path) == spec_path


def test_inject_bgm_missing_file_returns_original(tmp_path: Path):
    settings, spec_path = _make_context(tmp_path)
    (settings.data_dir / "pipeline_policy.json").write_text(
        PipelinePolicy(bgm_file="bgm.mp3").model_dump_json(),
        encoding="utf-8",
    )
    assert _inject(settings, spec_path) == spec_path


def test_inject_bgm_writes_render_copy(tmp_path: Path):
    settings, spec_path = _make_context(tmp_path)
    bgm_dir = settings.data_dir / "assets" / "bgm"
    bgm_dir.mkdir(parents=True)
    (bgm_dir / "bgm.mp3").write_bytes(b"fake-audio")
    (settings.data_dir / "pipeline_policy.json").write_text(
        PipelinePolicy(bgm_file="bgm.mp3").model_dump_json(),
        encoding="utf-8",
    )

    render_spec_path = _inject(settings, spec_path)

    assert render_spec_path != spec_path
    spec = json.loads(render_spec_path.read_text(encoding="utf-8"))
    assert spec["bgm"]["file"] == "bgm/sim-1/bgm.mp3"
    assert spec["bgm"]["volume"] == 0.15  # 无配音 → 正常音量
    assert spec["bgm"]["fade_out_seconds"] == 2.0
    assert Path(spec["bgm"]["source_path"]).is_file()
    # 原始 spec 不被污染
    assert "bgm" not in json.loads(spec_path.read_text(encoding="utf-8"))


def test_inject_bgm_lowers_volume_with_narration(tmp_path: Path):
    settings, spec_path = _make_context(tmp_path)
    spec_path.write_text(
        json.dumps({"simulation_id": "sim-1", "narration": {"audio": []}}),
        encoding="utf-8",
    )
    bgm_dir = settings.data_dir / "assets" / "bgm"
    bgm_dir.mkdir(parents=True)
    (bgm_dir / "bgm.mp3").write_bytes(b"fake-audio")
    (settings.data_dir / "pipeline_policy.json").write_text(
        PipelinePolicy(bgm_file="bgm.mp3").model_dump_json(),
        encoding="utf-8",
    )

    render_spec_path = _inject(settings, spec_path)
    spec = json.loads(render_spec_path.read_text(encoding="utf-8"))
    assert spec["bgm"]["volume"] == 0.08  # 有配音 → 压低避免盖过人声


# ---------- BGM 端点（列表/上传/试听） ----------

from fastapi.testclient import TestClient  # noqa: E402
from stock_video_generator.main import create_app  # noqa: E402


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="definitely-missing-node-for-test",
    )
    return TestClient(create_app(settings))


def test_bgm_list_empty_then_upload_appears(tmp_path: Path):
    client = _client(tmp_path)
    assert client.get("/api/settings/bgm/list").json() == []

    response = client.post(
        "/api/settings/bgm",
        files={"file": ("我的音乐.mp3", b"fake-mp3", "audio/mpeg")},
    )

    assert response.status_code == 200
    bgm_file = response.json()["bgm_file"]
    assert bgm_file.endswith(".mp3")
    assert bgm_file.isascii()  # 中文文件名已转为安全 ASCII 名
    listing = client.get("/api/settings/bgm/list").json()
    assert [item["file"] for item in listing] == [bgm_file]
    # 试听指定文件
    play = client.get("/api/settings/bgm", params={"file": bgm_file})
    assert play.status_code == 200
    assert play.content == b"fake-mp3"


def test_bgm_get_rejects_path_traversal(tmp_path: Path):
    client = _client(tmp_path)

    response = client.get("/api/settings/bgm", params={"file": "../secret.mp3"})

    assert response.status_code == 404
