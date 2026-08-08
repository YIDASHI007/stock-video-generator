from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import Button, Canvas, Frame, Label, PhotoImage, StringVar, Tk
from typing import Any

import psutil

BACKGROUND = "#0b0c12"
SURFACE = "#11121a"
LINE = "#292b38"
TEXT = "#f4f3f8"
MUTED = "#8f909d"
PURPLE = "#9b6cff"
GREEN = "#3be2a0"
YELLOW = "#f3c969"
RED = "#ff6e78"

UI_FONT = "Microsoft YaHei UI"
ICON_FONT = "Segoe Fluent Icons"
ICON_CHECK = "\ue73e"
ICON_ERROR = "\ue711"
ICON_PLAY = "\ue768"
ERROR_ALREADY_EXISTS = 183
SINGLE_INSTANCE_PREFIX = "Local\\StockVideoGenerator.Launcher"


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


def _is_expected_server(process: psutil.Process | None) -> bool:
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
    def __init__(self, parent: Any, count: int = 5) -> None:
        super().__init__(
            parent,
            width=94,
            height=14,
            background=BACKGROUND,
            highlightthickness=0,
            bd=0,
        )
        self.count = count
        self.set_step(0)

    def set_step(self, completed: int) -> None:
        self.delete("all")
        for index in range(self.count):
            x = 7 + index * 20
            color = PURPLE if index < completed else "#333541"
            self.create_oval(x - 3, 4, x + 3, 10, fill=color, outline="")


class LauncherWindow:
    def __init__(
        self, runtime_dir: Path, log_dir: Path, port: int, *, auto_start: bool = True
    ) -> None:
        self.runtime_dir = runtime_dir
        self.log_dir = log_dir
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.server_process: subprocess.Popen[bytes] | None = None
        self.update_manager: Any | None = None
        self.update_info: Any | None = None
        self.tray_icon: Any | None = None
        self._update_busy = False
        self._exiting = False
        self._motion_enabled = _client_animations_enabled()
        self._blocking_errors: list[str] = []
        self._snapshot: dict[str, Any] = {}

        self.root = Tk()
        self.root.title("工作台启动中心")
        self.window_icon = PhotoImage(file=str(_asset_path("launch-center-icon.png")))
        self.root.iconphoto(True, self.window_icon)
        self.root.configure(background=LINE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Escape>", self._handle_escape)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_window_close)

        width, height = 420, 300
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.status_text = StringVar(value="正在检查服务状态")
        self._build_ui()
        if auto_start:
            self.root.after(120, self._start_sequence)

    def _build_ui(self) -> None:
        shell = Frame(self.root, background=LINE, width=420, height=300)
        shell.pack(fill="both", expand=True)
        shell.pack_propagate(False)
        stage = Frame(shell, background=BACKGROUND, width=418, height=298)
        stage.place(x=1, y=1)
        stage.pack_propagate(False)

        Label(
            stage,
            text="股票视频工作台",
            foreground=MUTED,
            background=BACKGROUND,
            font=(UI_FONT, 9),
        ).place(relx=0.5, y=23, anchor="n")

        self.play_mark = FluentPlayMark(stage)
        self.play_mark.place(relx=0.5, y=56, anchor="n")

        self.status_row = Frame(
            stage,
            background=SURFACE,
            width=310,
            height=46,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.status_y = 184
        self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
        self.status_row.pack_propagate(False)

        self.status_content = Frame(self.status_row, background=SURFACE)
        self.status_content.place(relx=0.5, rely=0.5, anchor="center")
        self.status_label = StatusText(self.status_content, self.status_text)
        self.status_label.pack(side="left")
        self.status_indicator = BootIndicator(self.status_content)
        self.status_indicator.pack(side="left", padx=(8, 0))

        self.step_progress = StepProgress(stage)
        self.step_progress.place(relx=0.5, y=250, anchor="n")

        self.update_actions = Frame(stage, background=BACKGROUND)
        self.update_button = Button(
            self.update_actions,
            text="立即更新",
            command=self._install_update,
            background=PURPLE,
            activebackground="#ad8aff",
            foreground="#0b0c12",
            activeforeground="#0b0c12",
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
        expected = _is_expected_server(owner)
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
        return {"ready": ready, "owner": owner, "expected": _is_expected_server(owner)}

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

    def _check_update(self) -> tuple[Any, Any | None, str]:
        from velopack import GithubSource, UpdateManager

        from stock_video_generator.desktop import _update_repo_url

        repo_url = _update_repo_url(self.runtime_dir)
        if not repo_url:
            return None, None, "当前为开发模式"
        manager = UpdateManager(GithubSource(repo_url))
        if manager.get_is_portable():
            return manager, None, "当前为开发模式"
        update = manager.check_for_updates()
        return manager, update, manager.get_current_version()

    def _start_sequence(self) -> None:
        self._blocking_errors.clear()
        self._snapshot = {"ready": None, "owner": None, "expected": False}
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
                elif value.get("owner") and not value.get("expected"):
                    state = "error"
                    message = "服务端口被其他程序占用"
                    blocking_error = message
                else:
                    state = "error"
                    message = "服务启动失败"
                    blocking_error = message
            else:
                state = "error"
                message = "服务启动失败"
                blocking_error = message
        elif index == 1:
            owner = self._snapshot.get("owner")
            expected = self._snapshot.get("expected")
            if owner is not None and not expected and not self._snapshot.get("ready"):
                state = "error"
                message = "端口被其他程序占用"
                blocking_error = message
            else:
                message = "端口可用"
        elif index == 2:
            providers = value if ok and isinstance(value, list) else []
            provider_ok = any(
                item.get("available") for item in providers if isinstance(item, dict)
            )
            if provider_ok:
                message = "数据接口正常"
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
            else:
                manager, update, current = value
                self.update_manager = manager
                self.update_info = update
                if update is None:
                    message = "版本状态正常" if "开发" not in str(current) else "当前为开发模式"
                else:
                    message = f"发现新版本 v{update.TargetFullRelease.Version}"

        if blocking_error and blocking_error not in self._blocking_errors:
            self._blocking_errors.append(blocking_error)
        self.root.after(
            220,
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
        self.root.after(380, lambda: self._animate_exit(0, next_step))

    def _animate_exit(self, frame: int, next_step: Callable[[], None]) -> None:
        if not self._motion_enabled:
            self.status_row.place_forget()
            self.root.after(60, next_step)
            return
        if frame >= 8:
            self.status_row.place_forget()
            self.root.after(60, next_step)
            return
        self.status_row.place_configure(y=self.status_y - frame * 3)
        self.root.after(22, lambda: self._animate_exit(frame + 1, next_step))

    def _finish_sequence(self) -> None:
        if self._blocking_errors:
            self.status_text.set(f"{self._blocking_errors[0]} · 点击关闭")
            self.status_label.set_color(RED)
            self.status_indicator.set_result("error")
            self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
            self.root.bind("<Button-1>", lambda _event: self._exit_app())
            return
        self.step_progress.set_step(5)
        if self.update_manager is not None and self.update_info is not None:
            self._show_update_prompt()
            return
        self.status_text.set("检查完成，正在打开")
        self.status_label.set_color(GREEN)
        self.status_indicator.set_result("ok")
        self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
        self.root.after(480, self._open_workbench)

    def _show_update_prompt(self, *, failed: bool = False) -> None:
        version = self.update_info.TargetFullRelease.Version
        self.status_text.set(
            "更新失败，请重试" if failed else f"发现新版本 v{version}"
        )
        self.status_label.set_color(RED if failed else PURPLE)
        self.status_indicator.set_result("error" if failed else "warning")
        self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
        self.step_progress.place_forget()
        self.update_button.configure(
            text="重新更新" if failed else f"更新到 v{version}",
            state="normal",
        )
        self.later_button.configure(state="normal")
        self.update_actions.place(relx=0.5, y=246, anchor="n")
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
        self.update_actions.place_forget()
        version = self.update_info.TargetFullRelease.Version
        self._show_loading(f"正在下载 v{version}")
        self._background(
            lambda: self.update_manager.download_updates(self.update_info),
            self._on_update_downloaded,
        )

    def _on_update_downloaded(self, result: tuple[bool, Any]) -> None:
        if self._exiting:
            return
        ok, _value = result
        if not ok:
            self._show_update_prompt(failed=True)
            return
        self.status_text.set("下载完成，正在安装")
        self.status_label.set_color(GREEN)
        self.status_indicator.set_result("ok")
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
                MenuItem("打开工作台", self._tray_open, default=True),
                MenuItem("检查更新", self._tray_check_update),
                Menu.SEPARATOR,
                MenuItem("退出", self._tray_exit),
            )
            self.tray_icon = Icon(
                "stock-video-workbench",
                tray_image,
                f"股票视频工作台 · 端口 {self.port}",
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

    def _notify_tray(self, message: str) -> None:
        if self.tray_icon is None:
            return
        try:
            self.tray_icon.notify(message, "股票视频工作台")
        except Exception:
            pass

    def _tray_open(self, _icon: Any = None, _item: Any = None) -> None:
        webbrowser.open(self.base_url)

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
        manager, update, current = value
        self.update_manager = manager
        self.update_info = update
        if update is None:
            message = "当前为开发模式" if "开发" in str(current) else "当前已是最新版本"
            self._notify_tray(message)
            return
        version = update.TargetFullRelease.Version
        self._notify_tray(f"发现新版本 v{version}")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
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
        if not _is_expected_server(owner):
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

        def done(_result: tuple[bool, Any]) -> None:
            self.root.destroy()

        self._background(self._stop_service, done)

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
            webbrowser.open(self.base_url)
            self.status_text.set("系统托盘启动失败 · 点击退出")
            self.status_label.set_color(RED)
            self.status_indicator.set_result("error")
            self.status_row.place(relx=0.5, y=self.status_y, anchor="n")
            self.root.bind("<Button-1>", lambda _event: self._exit_app())
            return
        webbrowser.open(self.base_url)
        self.root.withdraw()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_launcher_gui(runtime_dir: Path, log_dir: Path, port: int) -> int:
    instance_guard = _acquire_single_instance(port)
    if instance_guard is None:
        webbrowser.open(f"http://127.0.0.1:{port}")
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
