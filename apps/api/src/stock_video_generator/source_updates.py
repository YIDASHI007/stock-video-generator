from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from stock_video_generator import __version__

VERSION_FILE = "apps/api/src/stock_video_generator/__init__.py"
VERSION_PATTERN = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


class SourceUpdateService:
    """Read-only Git update discovery for source-runtime installations."""

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir.resolve()

    def _git(self, *arguments: str, timeout: int = 20) -> str:
        git = shutil.which("git")
        if not git:
            raise RuntimeError("未找到 Git，无法检查源码更新。")
        result = subprocess.run(
            [git, *arguments],
            cwd=self.runtime_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or f"Git 命令失败：{' '.join(arguments)}")
        return result.stdout.strip()

    def check(self, *, refresh: bool = False) -> dict[str, Any]:
        if not (self.runtime_dir / ".git").is_dir():
            return {
                "mode": "installed",
                "state": "unsupported",
                "current_version": __version__,
                "latest_version": None,
                "update_available": False,
                "can_update": False,
                "dirty": False,
                "behind_commits": 0,
                "ahead_commits": 0,
                "release_notes": [],
                "message": "当前不是源码运行版。",
            }

        try:
            branch = self._git("branch", "--show-current")
            if not branch:
                raise RuntimeError("当前处于 detached HEAD，不能自动更新。")
            upstream = self._git(
                "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
            )
            if refresh:
                remote = upstream.split("/", 1)[0]
                self._git("fetch", "--quiet", remote, branch, timeout=30)

            tracked_changes = self._git("status", "--porcelain")
            dirty = bool(tracked_changes)
            behind = int(self._git("rev-list", "--count", f"HEAD..{upstream}") or 0)
            ahead = int(self._git("rev-list", "--count", f"{upstream}..HEAD") or 0)
            remote_source = self._git("show", f"{upstream}:{VERSION_FILE}")
            match = VERSION_PATTERN.search(remote_source)
            latest_version = match.group(1) if match else None
            notes = (
                self._git("log", "--format=%s", "-8", f"HEAD..{upstream}").splitlines()
                if behind
                else []
            )
            diverged = behind > 0 and ahead > 0
            update_available = behind > 0
            can_update = update_available and not dirty and not diverged
            if diverged:
                state = "diverged"
                message = "本地分支与远程分支已经分叉，需要人工合并。"
            elif dirty:
                state = "blocked"
                message = "检测到未提交的本地修改，已保护现场，不会自动覆盖。"
            elif update_available:
                state = "available"
                message = f"发现源码版 v{latest_version or '新版本'}，可增量更新。"
            elif ahead:
                state = "ahead"
                message = "本地源码包含尚未推送的提交。"
            else:
                state = "current"
                message = "当前源码已经是最新版本。"
            return {
                "mode": "source",
                "state": state,
                "branch": branch,
                "upstream": upstream,
                "current_version": __version__,
                "latest_version": latest_version,
                "update_available": update_available,
                "can_update": can_update,
                "dirty": dirty,
                "behind_commits": behind,
                "ahead_commits": ahead,
                "release_notes": notes,
                "message": message,
            }
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            return {
                "mode": "source",
                "state": "error",
                "current_version": __version__,
                "latest_version": None,
                "update_available": False,
                "can_update": False,
                "dirty": False,
                "behind_commits": 0,
                "ahead_commits": 0,
                "release_notes": [],
                "message": f"暂时无法检查源码更新：{exc}",
            }
