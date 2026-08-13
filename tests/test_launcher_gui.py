from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from stock_video_generator.launcher_gui import (
    DISPLAY_NAME,
    SINGLE_INSTANCE_PREFIX,
    SingleInstanceGuard,
    UpdateCheckResult,
    _acquire_single_instance,
    _estimate_update_size,
    _update_delivery_kind,
    _update_delivery_label,
    _format_download_eta,
    _format_download_size,
    _format_update_summary,
    _is_development_runtime,
    _is_expected_server,
    _release_api_url,
    _workbench_url,
    run_launcher_gui,
    run_update_download_worker,
)


class FakeProcess:
    def __init__(
        self, executable: Path, arguments: list[str], cwd: Path | None = None
    ) -> None:
        self.executable = executable
        self.arguments = arguments
        self.working_directory = cwd or executable.parent

    def exe(self) -> str:
        return str(self.executable)

    def cmdline(self) -> list[str]:
        return self.arguments

    def cwd(self) -> str:
        return str(self.working_directory)


def test_product_display_name_matches_social_media_positioning() -> None:
    assert DISPLAY_NAME == "社媒工作台"


def test_installed_server_is_recognized_across_launcher_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("stock_video_generator.launcher_gui.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    executable = tmp_path / "StockVideoGenerator" / "current" / "StockVideoGenerator.exe"
    process = FakeProcess(executable, [str(executable), "--serve", "--port", "8877"])

    assert _is_expected_server(process) is True


def test_unrelated_process_is_not_recognized_as_server(monkeypatch, tmp_path):
    monkeypatch.setattr("stock_video_generator.launcher_gui.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    executable = tmp_path / "OtherApp" / "StockVideoGenerator.exe"
    process = FakeProcess(executable, [str(executable), "--serve", "--port", "8877"])

    assert _is_expected_server(process) is False


def test_process_without_serve_flag_is_not_recognized(monkeypatch, tmp_path):
    monkeypatch.setattr("stock_video_generator.launcher_gui.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    executable = tmp_path / "StockVideoGenerator" / "current" / "StockVideoGenerator.exe"
    process = FakeProcess(executable, [str(executable), "--port", "8877"])

    assert _is_expected_server(process) is False


def test_development_server_is_recognized_from_runtime_directory(tmp_path):
    runtime_dir = tmp_path / "stock-video-generator-dev"
    executable = tmp_path / "runtime" / "python.exe"
    process = FakeProcess(
        executable,
        [
            str(executable),
            "-m",
            "stock_video_generator.desktop",
            "--serve",
            "--port",
            "8877",
        ],
        cwd=runtime_dir,
    )

    assert _is_expected_server(process, runtime_dir) is True


def test_release_api_url_accepts_github_repository() -> None:
    assert _release_api_url("https://github.com/example/releases") == (
        "https://api.github.com/repos/example/releases/releases/latest"
    )
    assert _release_api_url("https://example.com/example/releases") is None


def test_workbench_url_is_versioned_to_avoid_stale_browser_tabs() -> None:
    assert _workbench_url(8877) == "http://127.0.0.1:8877/?desktop=v0.1.13"


def test_development_mode_requires_git_checkout(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("stock_video_generator.launcher_gui.sys.frozen", False, raising=False)

    assert _is_development_runtime(tmp_path) is False
    (tmp_path / ".git").mkdir()
    assert _is_development_runtime(tmp_path) is True


def test_update_summary_explains_development_and_installed_modes() -> None:
    development = UpdateCheckResult(
        manager=None,
        update=None,
        current_version="0.1.13",
        mode="development",
        latest_version="0.1.6",
    )
    installed = UpdateCheckResult(
        manager=None,
        update=None,
        current_version="0.1.13",
        mode="installed",
    )

    assert _format_update_summary(development) == "开发版 v0.1.13 · 正式版 v0.1.6"
    assert _format_update_summary(installed) == "已是最新 v0.1.13"


def test_single_instance_guard_closes_created_mutex(monkeypatch):
    closed: list[int] = []
    monkeypatch.setattr("stock_video_generator.launcher_gui.sys.platform", "win32")
    monkeypatch.setattr(
        "stock_video_generator.launcher_gui._create_named_mutex",
        lambda name: (41, name != f"{SINGLE_INSTANCE_PREFIX}.8877"),
    )
    monkeypatch.setattr(
        "stock_video_generator.launcher_gui._close_named_mutex", closed.append
    )

    guard = _acquire_single_instance(8877)

    assert isinstance(guard, SingleInstanceGuard)
    guard.close()
    guard.close()
    assert closed == [41]


def test_duplicate_launcher_releases_duplicate_handle(monkeypatch):
    closed: list[int] = []
    monkeypatch.setattr("stock_video_generator.launcher_gui.sys.platform", "win32")
    monkeypatch.setattr(
        "stock_video_generator.launcher_gui._create_named_mutex",
        lambda _name: (73, True),
    )
    monkeypatch.setattr(
        "stock_video_generator.launcher_gui._close_named_mutex", closed.append
    )

    assert _acquire_single_instance(8877) is None
    assert closed == [73]


def test_duplicate_launcher_opens_existing_workbench_without_creating_window(
    monkeypatch, tmp_path
):
    opened: list[str] = []
    monkeypatch.setattr(
        "stock_video_generator.launcher_gui._acquire_single_instance",
        lambda _port: None,
    )
    monkeypatch.setattr(
        "stock_video_generator.launcher_gui.webbrowser.open",
        lambda url, **_kwargs: opened.append(url),
    )

    result = run_launcher_gui(tmp_path, tmp_path, 8877)

    assert result == 0
    assert opened == ["http://127.0.0.1:8877/?desktop=v0.1.13"]


def test_update_size_prefers_delta_chain() -> None:
    update = SimpleNamespace(
        DeltasToTarget=[SimpleNamespace(Size=120), SimpleNamespace(Size=80)],
        TargetFullRelease=SimpleNamespace(Size=900),
    )

    assert _estimate_update_size(update) == 200
    assert _update_delivery_kind(update) == "delta"
    assert _update_delivery_label(update) == "增量更新"


def test_update_size_falls_back_to_full_release() -> None:
    update = SimpleNamespace(
        DeltasToTarget=[], TargetFullRelease=SimpleNamespace(Size=4096)
    )

    assert _estimate_update_size(update) == 4096
    assert _update_delivery_kind(update) == "full"
    assert _update_delivery_label(update) == "完整更新"


def test_download_labels_are_compact_and_readable() -> None:
    assert _format_download_size(0) == "0B"
    assert _format_download_size(2.5 * 1024 * 1024) == "2.5MB"
    assert _format_download_eta(12) == "约 12 秒"
    assert _format_download_eta(125) == "约 2 分 05 秒"


def test_update_download_worker_reports_progress(monkeypatch, tmp_path) -> None:
    target = SimpleNamespace(Version="0.1.12", Size=1024)
    update = SimpleNamespace(DeltasToTarget=[], TargetFullRelease=target)

    class FakeUpdateManager:
        def __init__(self, _source) -> None:
            pass

        def get_is_portable(self) -> bool:
            return False

        def check_for_updates(self):
            return update

        def download_updates(self, _update, progress_callback) -> None:
            progress_callback(20)
            progress_callback(70)
            progress_callback(100)

    fake_module = SimpleNamespace(
        GithubSource=lambda url: url,
        UpdateManager=FakeUpdateManager,
    )
    monkeypatch.setitem(sys.modules, "velopack", fake_module)
    monkeypatch.setattr(
        "stock_video_generator.desktop._update_repo_url",
        lambda _runtime: "https://github.com/example/releases",
    )
    monkeypatch.setattr("stock_video_generator.desktop._log", lambda *_args: None)
    progress_file = tmp_path / "progress.json"

    result = run_update_download_worker(
        tmp_path, tmp_path, progress_file, requested_version="0.1.12"
    )

    payload = json.loads(progress_file.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["state"] == "complete"
    assert payload["progress"] == 100
    assert payload["total_bytes"] == 1024
    assert payload["version"] == "0.1.12"
    assert payload["delivery_kind"] == "full"
