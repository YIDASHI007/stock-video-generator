from __future__ import annotations

from pathlib import Path

from stock_video_generator.source_updates import SourceUpdateService


def test_non_source_runtime_is_reported_as_unsupported(tmp_path: Path) -> None:
    result = SourceUpdateService(tmp_path).check()

    assert result["mode"] == "installed"
    assert result["state"] == "unsupported"
    assert result["update_available"] is False


def test_clean_source_runtime_reports_available_update(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".git").mkdir()
    responses = {
        ("branch", "--show-current"): "main",
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): "origin/main",
        ("status", "--porcelain"): "",
        ("rev-list", "--count", "HEAD..origin/main"): "2",
        ("rev-list", "--count", "origin/main..HEAD"): "0",
        (
            "show",
            "origin/main:apps/api/src/stock_video_generator/__init__.py",
        ): '__version__ = "0.1.13"',
        ("log", "--format=%s", "-8", "HEAD..origin/main"): "feat: AI model settings\nfix: progress",
    }
    monkeypatch.setattr(
        SourceUpdateService,
        "_git",
        lambda self, *args, **kwargs: responses[args],
    )

    result = SourceUpdateService(tmp_path).check()

    assert result["state"] == "available"
    assert result["latest_version"] == "0.1.13"
    assert result["behind_commits"] == 2
    assert result["can_update"] is True
    assert result["release_notes"] == ["feat: AI model settings", "fix: progress"]


def test_dirty_source_runtime_blocks_automatic_update(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()

    def fake_git(self, *args, **kwargs):
        values = {
            ("branch", "--show-current"): "main",
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): "origin/main",
            ("status", "--porcelain"): " M local.py",
            ("rev-list", "--count", "HEAD..origin/main"): "1",
            ("rev-list", "--count", "origin/main..HEAD"): "0",
            (
                "show",
                "origin/main:apps/api/src/stock_video_generator/__init__.py",
            ): '__version__ = "0.1.13"',
            ("log", "--format=%s", "-8", "HEAD..origin/main"): "feat: update",
        }
        return values[args]

    monkeypatch.setattr(SourceUpdateService, "_git", fake_git)

    result = SourceUpdateService(tmp_path).check()

    assert result["state"] == "blocked"
    assert result["dirty"] is True
    assert result["can_update"] is False
