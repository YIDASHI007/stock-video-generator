from __future__ import annotations

from pathlib import Path

from stock_video_generator.launcher_gui import (
    SINGLE_INSTANCE_PREFIX,
    SingleInstanceGuard,
    _acquire_single_instance,
    _is_expected_server,
    run_launcher_gui,
)


class FakeProcess:
    def __init__(self, executable: Path, arguments: list[str]) -> None:
        self.executable = executable
        self.arguments = arguments

    def exe(self) -> str:
        return str(self.executable)

    def cmdline(self) -> list[str]:
        return self.arguments


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
        "stock_video_generator.launcher_gui.webbrowser.open", opened.append
    )

    result = run_launcher_gui(tmp_path, tmp_path, 8877)

    assert result == 0
    assert opened == ["http://127.0.0.1:8877"]
