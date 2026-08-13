from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import Button, Canvas, Frame, Label, PhotoImage, StringVar, Tk
from typing import Any

import psutil

from stock_video_generator import __version__

BACKGROUND = "#0c111b"
SURFACE = "#151d2a"
SURFACE_RAISED = "#1a2433"
LINE = "#2a374a"
TEXT = "#eef2fb"
MUTED = "#91a0b5"
PURPLE = "#7185ff"
GREEN = "#31c48d"
YELLOW = "#f1c66d"
RED = "#ff7180"

UI_FONT = "Microsoft YaHei UI"
DISPLAY_NAME = "社媒工作台"
ICON_FONT = "Segoe Fluent Icons"
ICON_CHECK = "\ue73e"
ICON_ERROR = "\ue711"
ICON_PLAY = "\ue768"
ERROR_ALREADY_EXISTS = 183
SINGLE_INSTANCE_PREFIX = "Local\\StockVideoGenerator.Launcher"
UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
UPDATE_PROGRESS_POLL_MS = 140
RELEASE_REPO_URL = "https://github.com/YIDASHI007/stock-video-generator-releases"
RELEASES_URL = f"{RELEASE_REPO_URL}/releases"


@dataclass(slots=True)
class UpdateCheckResult:
    manager: Any | None
    update: Any | None
    current_version: str
    mode: str
    latest_version: str | None = None
    detail: str = ""


def _is_development_runtime(runtime_dir: Path) -> bool:
    return not getattr(sys, "frozen", False) and (runtime_dir / ".git").is_dir()


def _runtime_mode(runtime_dir: Path) -> str:
    return "development" if _is_development_runtime(runtime_dir) else "installed"


def _release_api_url(repo_url: str) -> str | None:
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[:2]
    return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


def _latest_release_version(repo_url: str, timeout: float = 6) -> str | None:
    api_url = _release_api_url(repo_url)
    if api_url is None:
        return None
    try:
        payload = _http_json(api_url, timeout=timeout)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    return str(tag).lstrip("v") if tag else None


def _format_update_summary(result: UpdateCheckResult) -> str:
    current = result.current_version.lstrip("v") or __version__
    if result.update is not None:
        target = str(result.update.TargetFullRelease.Version).lstrip("v")
        return f"发现新版本 v{target}"
    if result.mode == "development":
        if result.latest_version:
            return f"开发版 v{current} · 正式版 v{result.latest_version}"
        return f"开发版 v{current} · 正式版状态未知"
    if result.mode == "portable":
        return f"便携版 v{current} · 不支持自动更新"
    if result.mode == "unconfigured":
        return f"v{current} · 未配置更新源"
    return f"已是最新 v{current}"


def _open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    webbrowser.open(path.resolve().as_uri())


def _workbench_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/?desktop=v{__version__}"


def _asset_path(name: str) -> Path:
    local = Path(__file__).resolve().parent / "assets" / name
    if local.is_file():
        return local
    frozen_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    bundled = frozen_root / "stock_video_generator" / "assets" / name
    return bundled if bundled.is_file() else local


def _client_animations_enabled() -> bool:
    if sys.platform != "win32":
        return True
    try:
        enabled = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return bool(enabled.value)
    except (AttributeError, OSError):
        return True


def _http_json(url: str, timeout: float = 5) -> dict[str, Any] | list[Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Workbench-Launcher"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _estimate_update_size(update_info: Any) -> int:
    deltas = list(getattr(update_info, "DeltasToTarget", None) or [])
    if deltas:
        total = sum(max(0, int(getattr(asset, "Size", 0) or 0)) for asset in deltas)
        if total > 0:
            return total
    target = getattr(update_info, "TargetFullRelease", None)
    return max(0, int(getattr(target, "Size", 0) or 0))


def _update_delivery_kind(update_info: Any) -> str:
    return "delta" if list(getattr(update_info, "DeltasToTarget", None) or []) else "full"


def _update_delivery_label(update_info: Any) -> str:
    return "增量更新" if _update_delivery_kind(update_info) == "delta" else "完整更新"


def _format_download_size(size: float) -> str:
    value = max(0.0, float(size))
    for suffix in ("B", "KB", "MB", "GB"):
        if value < 1024 or suffix == "GB":
            precision = 0 if suffix == "B" else 1
            return f"{value:.{precision}f}{suffix}"
        value /= 1024
    return "0B"


def _format_download_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "计算中"
    rounded = int(seconds + 0.5)
    if rounded < 60:
        return f"约 {max(1, rounded)} 秒"
    minutes, remainder = divmod(rounded, 60)
    if minutes < 60:
        return f"约 {minutes} 分 {remainder:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"约 {hours} 小时 {minutes:02d} 分"


def _write_update_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_update_download_worker(
    runtime_dir: Path,
    log_dir: Path,
    progress_path: Path,
    requested_version: str | None,
) -> int:
    """Download an update in an isolated process so the launcher can cancel it."""
    from velopack import GithubSource, UpdateManager

    from stock_video_generator.desktop import _log, _update_repo_url

    version = (requested_version or "").lstrip("v")

    def emit(
        state: str,
        *,
        progress: int = 0,
        total_bytes: int = 0,
        message: str = "",
        delivery_kind: str = "unknown",
    ) -> None:
        _write_update_progress(
            progress_path,
            {
                "state": state,
                "progress": max(0, min(100, int(progress))),
                "total_bytes": max(0, int(total_bytes)),
                "version": version,
                "message": message,
                "delivery_kind": delivery_kind,
                "updated_at": time.time(),
            },
        )

    try:
        emit("preparing", message="正在连接更新服务器")
        repo_url = _update_repo_url(runtime_dir)
        if not repo_url:
            raise RuntimeError("没有配置更新地址")
        manager = UpdateManager(GithubSource(repo_url))
        if manager.get_is_portable():
            raise RuntimeError("便携模式不支持自动更新")
        update = manager.check_for_updates()
        if update is None:
            raise RuntimeError("没有找到可下载的新版本")
        available_version = str(update.TargetFullRelease.Version).lstrip("v")
        if version and available_version != version:
            raise RuntimeError(
                f"目标版本已变化：请求 v{version}，当前为 v{available_version}"
            )
        version = available_version
        total_bytes = _estimate_update_size(update)
        delivery_kind = _update_delivery_kind(update)
        delivery_label = _update_delivery_label(update)
        emit(
            "downloading",
            total_bytes=total_bytes,
            message=f"开始下载{delivery_label}",
            delivery_kind=delivery_kind,
        )

        def on_progress(value: int) -> None:
            emit(
                "downloading",
                progress=value,
                total_bytes=total_bytes,
                message="正在下载更新",
                delivery_kind=delivery_kind,
            )

        manager.download_updates(update, progress_callback=on_progress)
        emit(
            "complete",
            progress=100,
            total_bytes=total_bytes,
            message="下载完成",
            delivery_kind=delivery_kind,
        )
        return 0
    except BaseException as exc:
        message = str(exc).strip() or exc.__class__.__name__
        try:
            emit("failed", message=message)
        except OSError:
            pass
        _log(log_dir, f"Update download worker failed: {exc!r}")
        return 1


def _listener_owner(port: int) -> psutil.Process | None:
    for connection in psutil.net_connections(kind="tcp"):
        if (
            connection.status == psutil.CONN_LISTEN
            and connection.laddr
            and connection.laddr.port == port
            and connection.pid
        ):
            try:
                return psutil.Process(connection.pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                return None
    return None


def _is_expected_server(
    process: psutil.Process | None, runtime_dir: Path | None = None
) -> bool:
    if process is None:
        return False
    try:
        executable = Path(process.exe()).resolve()
        arguments = process.cmdline()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return False
    if "--serve" not in arguments:
        return False
    expected = Path(sys.executable).resolve()
    if executable == expected:
        return True
    if runtime_dir is not None and "stock_video_generator.desktop" in arguments:
        try:
            if Path(process.cwd()).resolve() == runtime_dir.resolve():
                return True
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            pass
    if sys.platform != "win32" or executable.name.lower() != "stockvideogenerator.exe":
        return False
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return False
    install_root = (Path(local_app_data) / "StockVideoGenerator").resolve()
    return executable.is_relative_to(install_root)


def _create_named_mutex(name: str) -> tuple[int, bool]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    handle = create_mutex(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle), ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def _close_named_mutex(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_bool
    close_handle(ctypes.c_void_p(handle))


class SingleInstanceGuard:
    def __init__(self, handle: int | None = None) -> None:
        self.handle = handle

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            _close_named_mutex(handle)


def _acquire_single_instance(port: int) -> SingleInstanceGuard | None:
    if sys.platform != "win32":
        return SingleInstanceGuard()
    handle, already_exists = _create_named_mutex(f"{SINGLE_INSTANCE_PREFIX}.{port}")
    if already_exists:
        _close_named_mutex(handle)
        return None
    return SingleInstanceGuard(handle)


class StatusText(Label):
    def __init__(self, parent: Any, variable: StringVar) -> None:
        super().__init__(
            parent,
            textvariable=variable,
            background=SURFACE,
            bd=0,
            foreground=TEXT,
            font=(UI_FONT, 10),
            anchor="center",
        )

    def set_color(self, color: str) -> None:
        self.configure(foreground=color)


class BootIndicator(Canvas):
    def __init__(self, parent: Any, size: int = 30) -> None:
        super().__init__(
            parent,
            width=size,
            height=size,
            background=SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.size = size
        self._angle = 0
        self._timer: str | None = None
        self.set_loading()

    def _cancel(self) -> None:
        if self._timer is None:
            return
        try:
            self.after_cancel(self._timer)
        except Exception:
            pass
        self._timer = None

    def set_loading(self) -> None:
        self._cancel()
        self._draw_spinner()

    def _draw_spinner(self) -> None:
        self.delete("all")
        margin = 4
        self.create_oval(
            margin,
            margin,
            self.size - margin,
            self.size - margin,
            outline="#343644",
            width=2,
        )
        self.create_arc(
            margin,
            margin,
            self.size - margin,
            self.size - margin,
            start=self._angle,
            extent=100,
            style="arc",
            outline=PURPLE,
            width=3,
        )
        self._angle = (self._angle - 16) % 360
        self._timer = self.after(45, self._draw_spinner)

    def set_result(self, state: str) -> None:
        self._cancel()
        self.delete("all")
        color = GREEN if state == "ok" else YELLOW if state == "warning" else RED
        glyph = ICON_CHECK if state in {"ok", "warning"} else ICON_ERROR
        self.create_text(
            self.size / 2,
            self.size / 2,
            text=glyph,
            fill=color,
            font=(ICON_FONT, 16),
        )


class FluentPlayMark(Canvas):
    """A system-font play control; no raster resampling or hand-drawn SVG."""

    def __init__(self, parent: Any) -> None:
        super().__init__(
            parent,
            width=112,
            height=112,
            background=BACKGROUND,
            highlightthickness=0,
            bd=0,
        )
        self.create_oval(6, 6, 106, 106, fill=BACKGROUND, outline="#2d2a3b", width=1)
        self.create_oval(17, 17, 95, 95, fill="#171421", outline="#40365a", width=1)
        self.create_text(
            58,
            57,
            text=ICON_PLAY,
            fill=PURPLE,
            font=(ICON_FONT, 38),
            anchor="center",
        )


class StepProgress(Canvas):
    LABELS = ("服务", "端口", "数据", "媒体", "更新")

    def __init__(self, parent: Any) -> None:
        super().__init__(
            parent,
            width=442,
            height=52,
            background=BACKGROUND,
            highlightthickness=0,
            bd=0,
        )
        self.set_step(0)

    def set_step(self, active: int, *, failed: bool = False) -> None:
        self.delete("all")
        points = [23 + index * 99 for index in range(len(self.LABELS))]
        for index in range(len(points) - 1):
            color = GREEN if index < active else LINE
            self.create_line(points[index], 15, points[index + 1], 15, fill=color, width=2)
        for index, (x, label) in enumerate(zip(points, self.LABELS, strict=True)):
            if index < active:
                color = GREEN
            elif index == active and active < len(self.LABELS):
                color = RED if failed else PURPLE
            else:
                color = LINE
            radius = 6 if index == active and active < len(self.LABELS) else 4
            self.create_oval(
                x - radius,
                15 - radius,
                x + radius,
                15 + radius,
                fill=color,
                outline=BACKGROUND,
                width=2,
            )
            self.create_text(
                x,
                39,
                text=label,
                fill=TEXT if index == active else MUTED,
                font=(UI_FONT, 8, "bold" if index == active else "normal"),
            )


class DownloadProgress(Canvas):
    def __init__(self, parent: Any) -> None:
        super().__init__(
            parent,
            width=402,
            height=8,
            background=SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.set_value(0)

    def set_value(self, value: float) -> None:
        self.delete("all")
        width = 402
        height = 6
        progress = max(0.0, min(100.0, float(value)))
        self.create_rectangle(0, 1, width, height + 1, fill="#2b2d39", outline="")
        filled = int(width * progress / 100)
        if filled > 0:
            self.create_rectangle(0, 1, filled, height + 1, fill=PURPLE, outline="")


class LauncherWindow:
    def __init__(
        self, runtime_dir: Path, log_dir: Path, port: int, *, auto_start: bool = True
    ) -> None:
        self.runtime_dir = runtime_dir
        self.log_dir = log_dir
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.workbench_url = _workbench_url(port)
        self.server_process: subprocess.Popen[bytes] | None = None
        self.update_manager: Any | None = None
        self.update_info: Any | None = None
        self.update_status: UpdateCheckResult | None = None
        self.tray_icon: Any | None = None
        self.update_download_process: subprocess.Popen[bytes] | None = None
        self.update_progress_path = self.log_dir / "launcher-update-progress.json"
        self._download_cancelled = False
        self._download_last_time = 0.0
        self._download_last_bytes = 0.0
        self._download_speed = 0.0
        self._periodic_update_timer: str | None = None
        self._workbench_opened = False
        self._update_busy = False
        self._service_restart_busy = False
        self._exiting = False
        self._motion_enabled = _client_animations_enabled()
        self._blocking_errors: list[str] = []
        self._snapshot: dict[str, Any] = {}
        self._auto_open_after_checks = True
        self._drag_origin = (0, 0)
        self.mode = _runtime_mode(runtime_dir)

        self.root = Tk()
        self.root.title(f"{DISPLAY_NAME} · v{__version__}")
        self.window_icon = PhotoImage(file=str(_asset_path("launch-center-icon.png")))
        self.root.iconphoto(True, self.window_icon)
        self.root.configure(background=LINE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Escape>", self._handle_escape)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_window_close)

        width, height = 520, 360
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.status_text = StringVar(value="正在检查服务状态")
        self.status_detail_text = StringVar(value=f"本地服务 · 端口 {self.port}")
        self._build_ui()
        if auto_start:
            self.root.after(120, self._start_sequence)

    def _build_ui(self) -> None:
        shell = Frame(self.root, background=LINE, width=520, height=360)
        shell.pack(fill="both", expand=True)
        shell.pack_propagate(False)
        stage = Frame(shell, background=BACKGROUND, width=518, height=358)
        stage.place(x=1, y=1)
        stage.pack_propagate(False)

        title_bar = Frame(stage, background=BACKGROUND, width=518, height=78)
        title_bar.place(x=0, y=0)
        title_bar.pack_propagate(False)
        title_bar.bind("<ButtonPress-1>", self._begin_drag)
        title_bar.bind("<B1-Motion>", self._drag_window)

        self.brand_icon = PhotoImage(file=str(_asset_path("launch-center-icon.png"))).subsample(
            10, 10
        )
        Label(
            title_bar,
            image=self.brand_icon,
            background=BACKGROUND,
            bd=0,
        ).place(x=24, y=13)
        Label(
            title_bar,
            text=DISPLAY_NAME,
            foreground=TEXT,
            background=BACKGROUND,
            font=(UI_FONT, 14, "bold"),
        ).place(x=86, y=16)
        Label(
            title_bar,
            text="SOCIAL MEDIA OPERATIONS",
            foreground=MUTED,
            background=BACKGROUND,
            font=("Consolas", 8),
        ).place(x=87, y=45)

        badge = Frame(
            title_bar,
            background=SURFACE_RAISED,
            width=126,
            height=28,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        badge.place(x=344, y=20)
        badge.pack_propagate(False)
        mode_label = "DEV" if self.mode == "development" else "DESKTOP"
        Label(
            badge,
            text=f"{mode_label}  ·  v{__version__}",
            foreground=PURPLE if self.mode == "development" else GREEN,
            background=SURFACE_RAISED,
            font=("Consolas", 9, "bold"),
        ).place(relx=0.5, rely=0.5, anchor="center")
        Button(
            title_bar,
            text="×",
            command=self._handle_window_close,
            background=BACKGROUND,
            activebackground=SURFACE_RAISED,
            foreground=MUTED,
            activeforeground=TEXT,
            font=("Segoe UI", 15),
            relief="flat",
            bd=0,
            width=2,
            cursor="hand2",
            highlightthickness=0,
        ).place(x=478, y=12)
        Frame(stage, background=LINE, width=518, height=1).place(x=0, y=77)

        Label(
            stage,
            text="正在准备工作空间",
            foreground=TEXT,
            background=BACKGROUND,
            font=(UI_FONT, 10, "bold"),
        ).place(x=38, y=91)
        Label(
            stage,
            text="服务、内容引擎与更新状态",
            foreground=MUTED,
            background=BACKGROUND,
            font=(UI_FONT, 8),
        ).place(x=112, y=93)

        self.step_progress = StepProgress(stage)
        self.step_progress.place(x=38, y=113)

        self.status_row = Frame(
            stage,
            background=SURFACE,
            width=452,
            height=70,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.status_y = 174
        self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
        self.status_row.pack_propagate(False)

        self.status_content = Frame(self.status_row, background=SURFACE)
        self.status_content.place(x=18, rely=0.5, anchor="w")
        self.status_indicator = BootIndicator(self.status_content, size=32)
        self.status_indicator.pack(side="left", padx=(0, 12))
        status_copy = Frame(self.status_content, background=SURFACE)
        status_copy.pack(side="left")
        self.status_label = StatusText(status_copy, self.status_text)
        self.status_label.configure(font=(UI_FONT, 10, "bold"), anchor="w")
        self.status_label.pack(anchor="w")
        Label(
            status_copy,
            textvariable=self.status_detail_text,
            foreground=MUTED,
            background=SURFACE,
            font=(UI_FONT, 8),
            anchor="w",
        ).pack(anchor="w", pady=(5, 0))

        self.endpoint_text = StringVar(value=f"LOCALHOST  ·  {self.port}")
        Label(
            stage,
            textvariable=self.endpoint_text,
            foreground=MUTED,
            background=BACKGROUND,
            font=("Consolas", 8),
        ).place(x=38, y=258)
        Label(
            stage,
            text="关闭窗口后继续在系统托盘运行",
            foreground=MUTED,
            background=BACKGROUND,
            font=(UI_FONT, 8),
        ).place(x=480, y=258, anchor="ne")

        self.ready_actions = Frame(stage, background=BACKGROUND)
        self.open_workbench_button = Button(
            self.ready_actions,
            text="进入社媒工作台  →",
            command=self._open_workbench_from_center,
            background=GREEN,
            activebackground="#5bf0b9",
            foreground=BACKGROUND,
            activeforeground=BACKGROUND,
            font=(UI_FONT, 9, "bold"),
            relief="flat",
            bd=0,
            padx=20,
            pady=7,
            cursor="hand2",
            highlightthickness=0,
        )
        self.open_workbench_button.pack(side="left")
        self.recheck_button = Button(
            self.ready_actions,
            text="重新检查",
            command=lambda: self._start_sequence(auto_open=False),
            background=SURFACE_RAISED,
            activebackground=LINE,
            foreground=TEXT,
            activeforeground=TEXT,
            font=(UI_FONT, 9),
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
            highlightthickness=0,
        )
        self.recheck_button.pack(side="left", padx=(10, 0))
        self.open_logs_button = Button(
            self.ready_actions,
            text="查看日志",
            command=self._open_logs,
            background=BACKGROUND,
            activebackground=BACKGROUND,
            foreground=MUTED,
            activeforeground=TEXT,
            font=(UI_FONT, 9),
            relief="flat",
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            highlightthickness=0,
        )
        self.open_logs_button.pack(side="left", padx=(4, 0))

        self.update_actions = Frame(stage, background=BACKGROUND)
        self.update_button = Button(
            self.update_actions,
            text="立即更新",
            command=self._install_update,
            background=PURPLE,
            activebackground="#ad8aff",
            foreground=BACKGROUND,
            activeforeground=BACKGROUND,
            font=(UI_FONT, 9),
            relief="flat",
            bd=0,
            padx=15,
            pady=5,
            cursor="hand2",
            highlightthickness=0,
        )
        self.update_button.pack(side="left")
        self.later_button = Button(
            self.update_actions,
            text="暂不更新",
            command=self._open_workbench,
            background=BACKGROUND,
            activebackground=BACKGROUND,
            foreground=MUTED,
            activeforeground=TEXT,
            font=(UI_FONT, 9),
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            highlightthickness=0,
        )
        self.later_button.pack(side="left", padx=(8, 0))

        self.download_panel = Frame(
            stage,
            background=SURFACE,
            width=452,
            height=118,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.download_panel.pack_propagate(False)
        self.download_title_text = StringVar(value="正在准备更新")
        self.download_pct_text = StringVar(value="0%")
        self.download_detail_text = StringVar(value="正在连接更新服务器")
        self.download_rate_text = StringVar(value="")
        Label(
            self.download_panel,
            textvariable=self.download_title_text,
            foreground=TEXT,
            background=SURFACE,
            font=(UI_FONT, 10, "bold"),
        ).place(x=18, y=12)
        Label(
            self.download_panel,
            textvariable=self.download_pct_text,
            foreground=PURPLE,
            background=SURFACE,
            font=(UI_FONT, 9, "bold"),
        ).place(x=431, y=13, anchor="ne")
        self.download_progress = DownloadProgress(self.download_panel)
        self.download_progress.place(x=18, y=39)
        Label(
            self.download_panel,
            textvariable=self.download_detail_text,
            foreground=MUTED,
            background=SURFACE,
            font=(UI_FONT, 8),
        ).place(x=18, y=55)
        Label(
            self.download_panel,
            textvariable=self.download_rate_text,
            foreground=MUTED,
            background=SURFACE,
            font=(UI_FONT, 8),
        ).place(x=431, y=55, anchor="ne")
        self.cancel_download_button = Button(
            self.download_panel,
            text="取消下载",
            command=self._cancel_update_download,
            background="#242631",
            activebackground="#30323f",
            foreground=TEXT,
            activeforeground=TEXT,
            font=(UI_FONT, 8),
            relief="flat",
            bd=0,
            padx=12,
            pady=4,
            cursor="hand2",
            highlightthickness=0,
        )
        self.cancel_download_button.place(x=431, y=82, anchor="ne")

    def _begin_drag(self, event: Any) -> None:
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_window(self, event: Any) -> None:
        offset_x, offset_y = self._drag_origin
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _open_logs(self) -> None:
        _open_directory(self.log_dir)

    def _open_data(self) -> None:
        configured = os.environ.get("APP_DATA_DIR")
        _open_directory(Path(configured) if configured else self.runtime_dir / "data")

    def _open_outputs(self) -> None:
        configured = os.environ.get("APP_DATA_DIR")
        data_dir = Path(configured) if configured else self.runtime_dir / "data"
        _open_directory(data_dir / "outputs")

    def _open_workbench_from_center(self) -> None:
        self._open_workbench()

    def _hide_action_panels(self) -> None:
        self.ready_actions.place_forget()
        self.update_actions.place_forget()
        self.download_panel.place_forget()

    def _show_ready_actions(self, *, service_available: bool = True) -> None:
        self._hide_action_panels()
        self.open_workbench_button.configure(
            state="normal" if service_available else "disabled",
            background=GREEN if service_available else LINE,
        )
        self.ready_actions.place(relx=0.5, y=302, anchor="n")

    def _show_launch_center(self) -> None:
        if self._exiting:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self._start_sequence(auto_open=False)

    def _background(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        def runner() -> None:
            try:
                result: Any = (True, work())
            except Exception as exc:
                result = (False, exc)
            try:
                self.root.after(0, lambda: done(result))
            except Exception:
                pass

        threading.Thread(target=runner, daemon=True).start()

    def _read_ready(self) -> dict[str, Any] | None:
        try:
            payload = _http_json(f"{self.base_url}/ready", timeout=3)
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def _ensure_service(self) -> dict[str, Any]:
        ready = self._read_ready()
        owner = _listener_owner(self.port)
        expected = _is_expected_server(owner, self.runtime_dir)
        if ready:
            return {"ready": ready, "owner": owner, "expected": expected}
        if owner is not None and not expected:
            return {"ready": None, "owner": owner, "expected": False}
        if owner is not None and expected:
            try:
                owner.terminate()
                owner.wait(timeout=15)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass

        from stock_video_generator.desktop import _start_server, _wait_for_server

        self.server_process = _start_server(self.runtime_dir, self.log_dir, self.port)
        started = _wait_for_server(self.server_process, self.port)
        ready = self._read_ready() if started else None
        owner = _listener_owner(self.port)
        return {
            "ready": ready,
            "owner": owner,
            "expected": _is_expected_server(owner, self.runtime_dir),
        }

    def _provider_health(self) -> list[Any] | None:
        if not self._snapshot.get("ready"):
            return None
        payload = _http_json(f"{self.base_url}/api/providers/health", timeout=30)
        return payload if isinstance(payload, list) else None

    def _media_health(self) -> dict[str, Any] | None:
        if not self._snapshot.get("ready"):
            return None
        payload = _http_json(f"{self.base_url}/health", timeout=8)
        return payload if isinstance(payload, dict) else None

    def _check_update(self) -> UpdateCheckResult:
        from stock_video_generator.desktop import _update_repo_url

        repo_url = _update_repo_url(self.runtime_dir) or RELEASE_REPO_URL
        if self.mode == "development":
            latest = _latest_release_version(repo_url)
            return UpdateCheckResult(
                manager=None,
                update=None,
                current_version=__version__,
                mode="development",
                latest_version=latest,
                detail="开发版通过 Git 同步源码，不安装桌面更新包",
            )
        configured_repo = _update_repo_url(self.runtime_dir)
        if not configured_repo:
            return UpdateCheckResult(
                manager=None,
                update=None,
                current_version=__version__,
                mode="unconfigured",
                detail="当前运行目录没有配置 GitHub Release 更新源",
            )

        from velopack import GithubSource, UpdateManager

        manager = UpdateManager(GithubSource(configured_repo))
        current = str(manager.get_current_version() or __version__).lstrip("v")
        if manager.get_is_portable():
            return UpdateCheckResult(
                manager=manager,
                update=None,
                current_version=current,
                mode="portable",
                detail="便携运行模式不会修改本机安装目录",
            )
        update = manager.check_for_updates()
        latest = (
            str(update.TargetFullRelease.Version).lstrip("v") if update is not None else current
        )
        return UpdateCheckResult(
            manager=manager,
            update=update,
            current_version=current,
            mode="installed",
            latest_version=latest,
            detail="桌面安装版通过 GitHub Release 接收更新",
        )

    def _start_sequence(self, *, auto_open: bool = True) -> None:
        self._auto_open_after_checks = auto_open
        self._blocking_errors.clear()
        self._snapshot = {"ready": None, "owner": None, "expected": False}
        self._hide_action_panels()
        self.step_progress.place(x=38, y=113)
        self.step_progress.set_step(0)
        self.status_detail_text.set(f"本地服务 · 端口 {self.port}")
        self._run_step(0)

    def _run_step(self, index: int) -> None:
        steps: list[tuple[str, Callable[[], Any]]] = [
            ("正在检查服务状态", self._ensure_service),
            ("正在检查端口状态", lambda: self._snapshot.get("owner")),
            ("正在检查数据接口", self._provider_health),
            ("正在检查媒体组件", self._media_health),
            ("正在检查版本更新", self._check_update),
        ]
        if index >= len(steps):
            self._finish_sequence()
            return
        text, work = steps[index]
        self.step_progress.set_step(index)
        details = (
            f"正在连接 {self.base_url}",
            f"检查本机 TCP {self.port} 监听状态",
            "验证行情数据提供方是否可用",
            "确认 Node、Remotion 与 FFmpeg",
            f"核对当前 v{__version__} 与发布版本",
        )
        self.status_detail_text.set(details[index])
        self._show_loading(text)
        self._background(work, lambda result: self._handle_result(index, result))

    def _show_loading(self, text: str) -> None:
        self.status_text.set(text)
        self.status_label.set_color(TEXT)
        self.status_indicator.set_loading()
        self.status_row.place(relx=0.5, y=self.status_y, anchor="n")

    def _handle_result(self, index: int, result: tuple[bool, Any]) -> None:
        ok, value = result
        state = "ok"
        message = "检查完成"
        blocking_error: str | None = None

        if index == 0:
            if ok:
                self._snapshot.update(value)
                if value.get("ready"):
                    message = "服务状态正常"
                    version = value["ready"].get("version") or __version__
                    owner = value.get("owner")
                    owner_text = f" · PID {owner.pid}" if owner is not None else ""
                    self.status_detail_text.set(f"后台 v{version}{owner_text} · 端口 {self.port}")
                elif value.get("owner") and not value.get("expected"):
                    state = "error"
                    message = "服务端口被其他程序占用"
                    blocking_error = message
                    self.status_detail_text.set(
                        f"PID {value['owner'].pid} 正在监听 {self.port}，未自动终止"
                    )
                else:
                    state = "error"
                    message = "服务启动失败"
                    blocking_error = message
                    self.status_detail_text.set("后台没有通过 /ready 检查，请查看启动日志")
            else:
                state = "error"
                message = "服务启动失败"
                blocking_error = message
                self.status_detail_text.set("启动进程返回异常，请查看启动日志")
        elif index == 1:
            owner = self._snapshot.get("owner")
            expected = self._snapshot.get("expected")
            if owner is not None and not expected and not self._snapshot.get("ready"):
                state = "error"
                message = "端口被其他程序占用"
                blocking_error = message
            else:
                message = "端口可用"
                self.status_detail_text.set(f"127.0.0.1:{self.port} 已由工作台服务监听")
        elif index == 2:
            providers = value if ok and isinstance(value, list) else []
            provider_ok = any(
                item.get("available") for item in providers if isinstance(item, dict)
            )
            if provider_ok:
                message = "数据接口正常"
                available_count = sum(
                    1 for item in providers if isinstance(item, dict) and item.get("available")
                )
                self.status_detail_text.set(f"{available_count} 个行情数据源可以响应")
            elif self._snapshot.get("ready"):
                state = "error"
                message = "数据接口不可用"
                blocking_error = message
            else:
                state = "warning"
                message = "数据接口等待服务启动"
        elif index == 3:
            components = (value or {}).get("components", []) if ok else []
            required = {"node", "remotion", "ffmpeg"}
            available = {
                item.get("name")
                for item in components
                if isinstance(item, dict) and item.get("available")
            }
            if required.issubset(available):
                message = "媒体组件正常"
                self.status_detail_text.set("Node · Remotion · FFmpeg 均已就绪")
            elif self._snapshot.get("ready"):
                state = "error"
                message = "媒体组件不完整"
                blocking_error = message
            else:
                state = "warning"
                message = "媒体组件等待服务启动"
        else:
            if not ok:
                state = "warning"
                message = "暂时无法检查更新"
                self.status_detail_text.set("网络或 GitHub 更新源暂时没有响应")
            else:
                self.update_status = value
                self.update_manager = value.manager
                self.update_info = value.update
                message = _format_update_summary(value)
                self.status_detail_text.set(value.detail)

        if blocking_error and blocking_error not in self._blocking_errors:
            self._blocking_errors.append(blocking_error)
        if state == "error":
            self.step_progress.set_step(index, failed=True)
        else:
            self.step_progress.set_step(index + 1)
        self.root.after(
            90,
            lambda: self._complete_step(
                state,
                message,
                lambda: self._run_step(index + 1),
            ),
        )

    def _complete_step(
        self, state: str, message: str, next_step: Callable[[], None]
    ) -> None:
        color = GREEN if state == "ok" else RED if state == "error" else YELLOW
        self.status_text.set(message)
        self.status_label.set_color(color)
        self.status_indicator.set_result(state)
        self.root.after(150, lambda: self._animate_exit(0, next_step))

    def _animate_exit(self, frame: int, next_step: Callable[[], None]) -> None:
        if not self._motion_enabled:
            self.status_row.place_forget()
            self.root.after(60, next_step)
            return
        if frame >= 5:
            self.status_row.place_forget()
            self.root.after(60, next_step)
            return
        self.status_row.place_configure(y=self.status_y - frame * 3)
        self.root.after(16, lambda: self._animate_exit(frame + 1, next_step))

    def _finish_sequence(self) -> None:
        if self._blocking_errors:
            self.status_text.set(self._blocking_errors[0])
            self.status_label.set_color(RED)
            self.status_indicator.set_result("error")
            self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
            self._show_ready_actions(service_available=False)
            return
        self.step_progress.set_step(5)
        if self.update_manager is not None and self.update_info is not None:
            self._show_update_prompt()
            return
        self.status_text.set(
            _format_update_summary(self.update_status)
            if self.update_status is not None
            else "本机环境已就绪"
        )
        self.status_detail_text.set(f"工作台 v{__version__} · 本地服务运行正常")
        self.status_label.set_color(GREEN)
        self.status_indicator.set_result("ok")
        self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
        if self._auto_open_after_checks:
            self.root.after(260, self._open_workbench)
        else:
            self._show_ready_actions()

    def _show_update_prompt(
        self, *, failed: bool = False, cancelled: bool = False
    ) -> None:
        version = self.update_info.TargetFullRelease.Version
        if failed:
            message = "更新失败，请重试"
        elif cancelled:
            message = "下载已取消"
        else:
            message = f"发现新版本 v{version}"
        self.status_text.set(message)
        delivery_label = _update_delivery_label(self.update_info)
        update_size = _format_download_size(_estimate_update_size(self.update_info))
        self.status_detail_text.set(
            f"当前 v{__version__} · {delivery_label} {update_size} · 可升级到 v{version}"
        )
        self.status_label.set_color(RED if failed else PURPLE)
        self.status_indicator.set_result("error" if failed else "warning")
        self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
        self.download_panel.place_forget()
        self.step_progress.place_forget()
        self.update_button.configure(
            text=(
                "重新更新"
                if failed
                else "重新下载"
                if cancelled
                else f"更新到 v{version}"
            ),
            state="normal",
        )
        self.later_button.configure(state="normal")
        self.update_actions.place(relx=0.5, y=302, anchor="n")
        self.cancel_download_button.configure(text="取消下载", state="normal")
        self._update_busy = False

    def _install_update(self) -> None:
        if (
            self._exiting
            or self._update_busy
            or self.update_manager is None
            or self.update_info is None
        ):
            return
        self._update_busy = True
        self._download_cancelled = False
        self.update_actions.place_forget()
        self.status_row.place_forget()
        self.step_progress.place_forget()
        version = self.update_info.TargetFullRelease.Version
        total_bytes = _estimate_update_size(self.update_info)
        delivery_label = _update_delivery_label(self.update_info)
        self.download_title_text.set(f"正在准备{delivery_label} v{version}")
        self.download_pct_text.set("0%")
        self.download_detail_text.set(
            f"0B / {_format_download_size(total_bytes)}"
            if total_bytes
            else "正在连接更新服务器"
        )
        self.download_rate_text.set("")
        self.download_progress.set_value(0)
        self.cancel_download_button.configure(text="取消下载", state="normal")
        self.cancel_download_button.place(x=431, y=82, anchor="ne")
        self.download_panel.place(relx=0.5, y=174, anchor="n")
        self._start_update_download_process(version)

    def _start_update_download_process(self, version: Any) -> None:
        from stock_video_generator.desktop import _self_command

        try:
            self.update_progress_path.unlink(missing_ok=True)
        except OSError:
            pass
        command = _self_command(
            "--download-update",
            "--update-version",
            str(version),
            "--update-progress-file",
            str(self.update_progress_path),
        )
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        try:
            self.update_download_process = subprocess.Popen(
                command,
                cwd=self.runtime_dir,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError:
            self.update_download_process = None
            self._show_update_prompt(failed=True)
            return
        self._download_last_time = time.monotonic()
        self._download_last_bytes = 0.0
        self._download_speed = 0.0
        self.root.after(UPDATE_PROGRESS_POLL_MS, self._poll_update_download)

    def _read_update_progress(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.update_progress_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError):
            return None

    def _poll_update_download(self) -> None:
        if self._exiting or self._download_cancelled:
            return
        process = self.update_download_process
        if process is None:
            return
        payload = self._read_update_progress()
        if payload:
            self._update_download_ui(payload)
        return_code = process.poll()
        if return_code is None:
            self.root.after(UPDATE_PROGRESS_POLL_MS, self._poll_update_download)
            return
        self.update_download_process = None
        state = str((payload or {}).get("state", ""))
        if return_code == 0 and state == "complete":
            self._on_update_downloaded((True, payload))
        else:
            self._show_update_prompt(failed=True)

    def _update_download_ui(self, payload: dict[str, Any]) -> None:
        state = str(payload.get("state", ""))
        progress = max(0, min(100, int(payload.get("progress", 0) or 0)))
        total_bytes = max(0, int(payload.get("total_bytes", 0) or 0))
        version = str(payload.get("version", "")).lstrip("v")
        delivery_kind = str(payload.get("delivery_kind", "unknown"))
        delivery_label = "增量更新" if delivery_kind == "delta" else "完整更新"
        downloaded = total_bytes * progress / 100
        now = time.monotonic()
        elapsed = now - self._download_last_time
        if elapsed >= 0.35 and downloaded >= self._download_last_bytes:
            instant_speed = (downloaded - self._download_last_bytes) / elapsed
            if instant_speed > 0:
                self._download_speed = (
                    instant_speed
                    if self._download_speed <= 0
                    else self._download_speed * 0.7 + instant_speed * 0.3
                )
            self._download_last_time = now
            self._download_last_bytes = downloaded

        if state == "preparing":
            self.download_title_text.set(f"正在准备 v{version}" if version else "正在准备更新")
            self.download_detail_text.set("正在连接更新服务器")
        elif state == "complete":
            self.download_title_text.set("下载完成，正在安装")
        else:
            self.download_title_text.set(
                f"正在下载{delivery_label} v{version}" if version else f"正在下载{delivery_label}"
            )
        self.download_pct_text.set(f"{progress}%")
        self.download_progress.set_value(progress)
        if total_bytes:
            self.download_detail_text.set(
                f"{_format_download_size(downloaded)} / {_format_download_size(total_bytes)}"
            )
        if self._download_speed > 0 and total_bytes > downloaded:
            eta = (total_bytes - downloaded) / self._download_speed
            self.download_rate_text.set(
                f"{_format_download_size(self._download_speed)}/s"
                f" · 剩余 {_format_download_eta(eta)}"
            )
        elif state == "downloading":
            self.download_rate_text.set("正在计算速度")

    def _cancel_update_download(self) -> None:
        process = self.update_download_process
        if self._exiting or process is None or process.poll() is not None:
            return
        self._download_cancelled = True
        self.cancel_download_button.configure(text="正在取消…", state="disabled")
        self._background(self._stop_update_download_process, self._after_download_cancelled)

    def _stop_update_download_process(self) -> None:
        process = self.update_download_process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _after_download_cancelled(self, _result: tuple[bool, Any]) -> None:
        self.update_download_process = None
        try:
            self.update_progress_path.unlink(missing_ok=True)
        except OSError:
            pass
        if not self._exiting:
            self._show_update_prompt(cancelled=True)

    def _on_update_downloaded(self, result: tuple[bool, Any]) -> None:
        if self._exiting:
            return
        ok, _value = result
        if not ok:
            self._show_update_prompt(failed=True)
            return
        self.download_title_text.set("下载完成，正在安装")
        self.download_pct_text.set("100%")
        self.download_progress.set_value(100)
        self.download_detail_text.set("安装完成后工作台将自动重启")
        self.download_rate_text.set("")
        self.cancel_download_button.place_forget()
        self.root.after(320, self._apply_update)

    def _apply_update(self) -> None:
        def work() -> None:
            self._stop_service()
            self._stop_tray()
            self.update_manager.apply_updates_and_restart(self.update_info)

        self._background(work, self._on_update_applied)

    def _on_update_applied(self, result: tuple[bool, Any]) -> None:
        ok, _value = result
        if ok:
            self.root.destroy()
            return
        self.server_process = None
        self._background(self._ensure_service, self._after_update_recovery)

    def _after_update_recovery(self, _result: tuple[bool, Any]) -> None:
        if self._exiting:
            return
        self._ensure_tray()
        self._show_update_prompt(failed=True)

    def _ensure_tray(self) -> bool:
        if self.tray_icon is not None:
            return True
        try:
            from PIL import Image
            from pystray import Icon, Menu, MenuItem

            with Image.open(_asset_path("launch-center-icon.png")) as source:
                tray_image = source.convert("RGBA").copy()
            menu = Menu(
                MenuItem(self._tray_version_text, None, enabled=False),
                MenuItem(self._tray_service_text, None, enabled=False),
                Menu.SEPARATOR,
                MenuItem("打开社媒工作台", self._tray_open, default=True),
                MenuItem("显示启动中心", self._tray_show_center),
                MenuItem("重启后台服务", self._tray_restart_service),
                MenuItem("检查版本更新", self._tray_check_update),
                Menu.SEPARATOR,
                MenuItem("打开输出目录", self._tray_open_outputs),
                MenuItem("打开数据目录", self._tray_open_data),
                MenuItem("打开日志目录", self._tray_open_logs),
                MenuItem("查看发布说明", self._tray_release_notes),
                Menu.SEPARATOR,
                MenuItem("退出工作台", self._tray_exit),
            )
            self.tray_icon = Icon(
                "stock-video-workbench",
                tray_image,
                f"{DISPLAY_NAME} v{__version__} · 端口 {self.port}",
                menu,
            )
            self.tray_icon.run_detached()
            return True
        except Exception as exc:
            self.tray_icon = None
            try:
                from stock_video_generator.desktop import _log

                _log(self.log_dir, f"Tray startup failed: {exc!r}")
            except Exception:
                pass
            return False

    def _tray_version_text(self, _item: Any = None) -> str:
        mode = "开发版" if self.mode == "development" else "桌面版"
        return f"{DISPLAY_NAME} v{__version__} · {mode}"

    def _tray_service_text(self, _item: Any = None) -> str:
        if self._service_restart_busy:
            return f"◌ 后台服务正在重启 · {self.port}"
        if self._snapshot.get("ready"):
            return f"● 后台服务正常 · {self.port}"
        return f"○ 后台服务未就绪 · {self.port}"

    def _refresh_tray_menu(self) -> None:
        if self.tray_icon is None:
            return
        try:
            self.tray_icon.update_menu()
        except Exception:
            pass

    def _notify_tray(self, message: str) -> None:
        if self.tray_icon is None:
            return
        try:
            self.tray_icon.notify(message, DISPLAY_NAME)
        except Exception:
            pass

    def _tray_open(self, _icon: Any = None, _item: Any = None) -> None:
        self._workbench_opened = True
        webbrowser.open(self.workbench_url, new=2)

    def _tray_show_center(self, _icon: Any = None, _item: Any = None) -> None:
        self.root.after(0, self._show_launch_center)

    def _tray_open_outputs(self, _icon: Any = None, _item: Any = None) -> None:
        self.root.after(0, self._open_outputs)

    def _tray_open_data(self, _icon: Any = None, _item: Any = None) -> None:
        self.root.after(0, self._open_data)

    def _tray_open_logs(self, _icon: Any = None, _item: Any = None) -> None:
        self.root.after(0, self._open_logs)

    def _tray_release_notes(self, _icon: Any = None, _item: Any = None) -> None:
        webbrowser.open(RELEASES_URL)

    def _tray_restart_service(self, _icon: Any = None, _item: Any = None) -> None:
        if self._exiting or self._service_restart_busy:
            self._notify_tray("后台服务正在重启")
            return
        self._service_restart_busy = True
        self._refresh_tray_menu()
        self._notify_tray("正在重启后台服务")
        self._background(self._restart_service, self._handle_service_restart_result)

    def _restart_service(self) -> dict[str, Any]:
        self._stop_service()
        self.server_process = None
        return self._ensure_service()

    def _handle_service_restart_result(self, result: tuple[bool, Any]) -> None:
        if self._exiting:
            return
        ok, value = result
        self._service_restart_busy = False
        if ok and isinstance(value, dict):
            self._snapshot.update(value)
        ready = bool(ok and isinstance(value, dict) and value.get("ready"))
        self._notify_tray(
            "后台服务已重新启动" if ready else "后台服务重启失败，请查看日志"
        )
        self._refresh_tray_menu()

    def _tray_check_update(self, _icon: Any = None, _item: Any = None) -> None:
        if self._exiting or self._update_busy:
            self._notify_tray("正在检查或安装更新")
            return
        self._update_busy = True
        self._notify_tray("正在检查更新")
        self._background(self._check_update, self._handle_tray_update_result)

    def _handle_tray_update_result(self, result: tuple[bool, Any]) -> None:
        if self._exiting:
            return
        ok, value = result
        self._update_busy = False
        if not ok:
            self._notify_tray("暂时无法检查更新，请稍后重试")
            return
        self.update_status = value
        self.update_manager = value.manager
        self.update_info = value.update
        self._refresh_tray_menu()
        if value.update is None:
            self._notify_tray(_format_update_summary(value))
            return
        version = value.update.TargetFullRelease.Version
        self._notify_tray(f"发现新版本 v{version}")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self._show_update_prompt()

    def _schedule_periodic_update_check(
        self, delay_ms: int = UPDATE_CHECK_INTERVAL_MS
    ) -> None:
        if self._exiting:
            return
        if self._periodic_update_timer is not None:
            try:
                self.root.after_cancel(self._periodic_update_timer)
            except Exception:
                pass
        self._periodic_update_timer = self.root.after(
            delay_ms, self._run_periodic_update_check
        )

    def _run_periodic_update_check(self) -> None:
        self._periodic_update_timer = None
        if self._exiting:
            return
        if self._update_busy:
            self._schedule_periodic_update_check(5 * 60 * 1000)
            return
        self._update_busy = True
        self._background(self._check_update, self._handle_periodic_update_result)

    def _handle_periodic_update_result(self, result: tuple[bool, Any]) -> None:
        if self._exiting:
            return
        ok, value = result
        self._update_busy = False
        if not ok:
            self._schedule_periodic_update_check()
            return
        self.update_status = value
        self.update_manager = value.manager
        self.update_info = value.update
        self._refresh_tray_menu()
        if value.update is None:
            self._schedule_periodic_update_check()
            return
        version = value.update.TargetFullRelease.Version
        self._notify_tray(f"发现新版本 v{version}，可立即下载更新")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self._show_update_prompt()

    def _tray_exit(self, _icon: Any = None, _item: Any = None) -> None:
        try:
            self.root.after(0, self._exit_app)
        except Exception:
            self._exit_app()

    def _stop_tray(self) -> None:
        icon, self.tray_icon = self.tray_icon, None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            pass

    def _stop_service(self) -> None:
        process = self.server_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
            return
        owner = _listener_owner(self.port)
        if not _is_expected_server(owner, self.runtime_dir):
            return
        owner.terminate()
        try:
            owner.wait(timeout=30)
        except psutil.TimeoutExpired:
            owner.kill()

    def _exit_app(self) -> None:
        if self._exiting:
            return
        self._exiting = True
        self._stop_tray()
        if self._periodic_update_timer is not None:
            try:
                self.root.after_cancel(self._periodic_update_timer)
            except Exception:
                pass
            self._periodic_update_timer = None

        def done(_result: tuple[bool, Any]) -> None:
            self.root.destroy()

        def work() -> None:
            self._stop_update_download_process()
            self._stop_service()

        self._background(work, done)

    def _handle_escape(self, _event: Any = None) -> None:
        if self.tray_icon is None:
            self._exit_app()
            return
        self.root.withdraw()

    def _handle_window_close(self) -> None:
        if self.tray_icon is None:
            self._exit_app()
            return
        self.root.withdraw()

    def _open_workbench(self) -> None:
        if not self._ensure_tray():
            webbrowser.open(self.workbench_url, new=2)
            self._workbench_opened = True
            self.status_text.set("系统托盘启动失败")
            self.status_detail_text.set("工作台已在浏览器打开；启动中心需保持运行")
            self.status_label.set_color(RED)
            self.status_indicator.set_result("error")
            self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
            self._show_ready_actions()
            return
        if not self._workbench_opened:
            webbrowser.open(self.workbench_url, new=2)
            self._workbench_opened = True
        self._schedule_periodic_update_check()
        self.root.withdraw()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_launcher_gui(runtime_dir: Path, log_dir: Path, port: int) -> int:
    instance_guard = _acquire_single_instance(port)
    if instance_guard is None:
        webbrowser.open(_workbench_url(port), new=2)
        return 0
    try:
        return LauncherWindow(runtime_dir, log_dir, port).run()
    except Exception as exc:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "launcher-gui-error.log").open("a", encoding="utf-8") as handle:
                handle.write(f"{exc!r}\n")
        except OSError:
            pass
        return 1
    finally:
        instance_guard.close()
