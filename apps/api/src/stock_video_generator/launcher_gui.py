from __future__ import annotations

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
from tkinter import (
    BOTH,
    DISABLED,
    LEFT,
    NORMAL,
    RIGHT,
    Button,
    Frame,
    Label,
    StringVar,
    Tk,
    X,
    messagebox,
)
from typing import Any

import psutil

from stock_video_generator import __version__

BACKGROUND = "#071019"
PANEL = "#0d1a24"
PANEL_ALT = "#10222e"
LINE = "#1d3442"
TEXT = "#eef6f3"
MUTED = "#7f99a7"
GREEN = "#35e6a4"
YELLOW = "#f3c969"
RED = "#ff6e78"
BLUE = "#71c9ff"


def _http_json(url: str, timeout: float = 5) -> dict[str, Any] | list[Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "StockVideoGenerator-GUI"},
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
    expected = Path(sys.executable).resolve()
    return executable == expected and "--serve" in arguments


class LauncherWindow:
    def __init__(self, runtime_dir: Path, log_dir: Path, port: int) -> None:
        self.runtime_dir = runtime_dir
        self.log_dir = log_dir
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.data_dir = Path(os.environ["APP_DATA_DIR"])
        self.server_process: subprocess.Popen[bytes] | None = None
        self.update_manager: Any | None = None
        self.update_info: Any | None = None
        self.busy = False

        self.root = Tk()
        self.root.title("股票回测视频生成器 · 启动中心")
        self.root.geometry("980x680")
        self.root.minsize(880, 620)
        self.root.configure(background=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self.version_text = StringVar(value=f"本机版本  ·  v{__version__}")
        self.last_check_text = StringVar(value="正在读取本机状态")
        self.update_title = StringVar(value="正在检查更新")
        self.update_detail = StringVar(value="连接 GitHub 正式发布仓库…")
        self.service_title = StringVar(value="正在检查后台服务")
        self.service_detail = StringVar(value=f"检查 127.0.0.1:{port}")
        self.data_title = StringVar(value="正在检查用户数据")
        self.data_detail = StringVar(value=str(self.data_dir))
        self.provider_text = StringVar(value="行情数据源  ·  等待后端")
        self.media_text = StringVar(value="媒体运行库  ·  等待后端")
        self.footer_text = StringVar(value="启动中心关闭后，已经运行的后台服务会继续工作")

        self._build_ui()
        self.root.after(200, self.refresh_all)

    def _label(
        self,
        parent: Any,
        text: str | None = None,
        *,
        variable: StringVar | None = None,
        color: str = TEXT,
        size: int = 12,
        bold: bool = False,
    ) -> Label:
        return Label(
            parent,
            text=text,
            textvariable=variable,
            background=parent.cget("background"),
            foreground=color,
            font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
            anchor="w",
        )

    def _button(
        self,
        parent: Any,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
    ) -> Button:
        return Button(
            parent,
            text=text,
            command=command,
            background=GREEN if primary else "#142632",
            foreground="#042017" if primary else TEXT,
            activebackground="#63f0bb" if primary else "#1b3441",
            activeforeground="#042017" if primary else TEXT,
            relief="flat",
            bd=0,
            padx=18,
            pady=10,
            cursor="hand2",
            font=("Microsoft YaHei UI", 11, "bold"),
        )

    def _card(self, parent: Any) -> Frame:
        return Frame(parent, background=PANEL, highlightbackground=LINE, highlightthickness=1)

    def _build_ui(self) -> None:
        shell = Frame(self.root, background=BACKGROUND, padx=34, pady=28)
        shell.pack(fill=BOTH, expand=True)

        header = Frame(shell, background=BACKGROUND)
        header.pack(fill=X, pady=(0, 20))
        logo = Label(
            header,
            text="↗",
            background=GREEN,
            foreground="#05241a",
            width=2,
            height=1,
            font=("Microsoft YaHei UI", 25, "bold"),
        )
        logo.pack(side=LEFT, padx=(0, 14))
        heading = Frame(header, background=BACKGROUND)
        heading.pack(side=LEFT)
        self._label(heading, "股票回测视频生成器", size=21, bold=True).pack(anchor="w")
        self._label(
            heading,
            "D E S K T O P   L A U N C H   C E N T E R",
            color="#5e8192",
            size=8,
        ).pack(anchor="w", pady=(4, 0))
        meta = Frame(header, background=BACKGROUND)
        meta.pack(side=RIGHT)
        self._label(meta, variable=self.version_text, color="#91aab5", size=10).pack(anchor="e")
        self._label(meta, variable=self.last_check_text, color="#4f6b79", size=9).pack(
            anchor="e", pady=(4, 0)
        )

        self.update_panel = Frame(
            shell,
            background="#102920",
            highlightbackground="#1d6049",
            highlightthickness=1,
            padx=16,
            pady=13,
        )
        self.update_panel.pack(fill=X, pady=(0, 18))
        update_copy = Frame(self.update_panel, background=self.update_panel.cget("background"))
        update_copy.pack(side=LEFT, fill=X, expand=True)
        self.update_title_label = self._label(
            update_copy, variable=self.update_title, size=11, bold=True
        )
        self.update_title_label.pack(anchor="w")
        self.update_detail_label = self._label(
            update_copy, variable=self.update_detail, color="#7f9f93", size=9
        )
        self.update_detail_label.pack(anchor="w", pady=(3, 0))
        self.update_button = self._button(
            self.update_panel, "一键更新", self.install_update
        )
        self.update_button.configure(state=DISABLED)
        self.update_button.pack(side=RIGHT, padx=(9, 0))
        self.check_update_button = self._button(
            self.update_panel, "↻  检查更新", self.check_updates
        )
        self.check_update_button.pack(side=RIGHT)

        content = Frame(shell, background=BACKGROUND)
        content.pack(fill=BOTH, expand=True)
        left = self._card(content)
        right = self._card(content)
        left.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 9))
        right.pack(side=LEFT, fill=BOTH, expand=True, padx=(9, 0))

        service_body = Frame(left, background=PANEL, padx=22, pady=20)
        service_body.pack(fill=BOTH, expand=True)
        self._label(service_body, "本机服务", color="#80a2b2", size=9, bold=True).pack(anchor="w")
        self.service_title_label = self._label(
            service_body, variable=self.service_title, size=16, bold=True
        )
        self.service_title_label.pack(anchor="w", pady=(14, 5))
        self._label(
            service_body,
            variable=self.service_detail,
            color=MUTED,
            size=10,
        ).pack(anchor="w")
        checks = Frame(service_body, background=PANEL_ALT, padx=15, pady=13)
        checks.pack(fill=X, pady=(20, 18))
        self.provider_label = self._label(checks, variable=self.provider_text, color=MUTED, size=10)
        self.provider_label.pack(anchor="w", pady=(0, 10))
        self.media_label = self._label(checks, variable=self.media_text, color=MUTED, size=10)
        self.media_label.pack(anchor="w")
        actions = Frame(service_body, background=PANEL)
        actions.pack(fill=X, side="bottom")
        self.service_button = self._button(actions, "启动后台服务", self.start_or_restart)
        self.service_button.pack(side=LEFT)
        self.refresh_button = self._button(actions, "重新检查", self.refresh_all)
        self.refresh_button.pack(side=LEFT, padx=(9, 0))

        data_body = Frame(right, background=PANEL, padx=22, pady=20)
        data_body.pack(fill=BOTH, expand=True)
        self._label(data_body, "工作区", color="#80a2b2", size=9, bold=True).pack(anchor="w")
        self.data_title_label = self._label(
            data_body, variable=self.data_title, size=16, bold=True
        )
        self.data_title_label.pack(anchor="w", pady=(14, 5))
        self._label(data_body, variable=self.data_detail, color=MUTED, size=9).pack(anchor="w")
        tip = Frame(data_body, background=PANEL_ALT, padx=15, pady=13)
        tip.pack(fill=X, pady=(20, 18))
        self._label(tip, "网页工作台", size=11, bold=True).pack(anchor="w")
        self._label(
            tip,
            f"{self.base_url}\n后台就绪后再打开，不会重复启动服务。",
            color=MUTED,
            size=9,
        ).pack(anchor="w", pady=(6, 0))
        data_actions = Frame(data_body, background=PANEL)
        data_actions.pack(fill=X, side="bottom")
        self.open_button = self._button(
            data_actions, "打开生产工作台  →", self.open_workbench, primary=True
        )
        self.open_button.configure(state=DISABLED)
        self.open_button.pack(side=LEFT)
        self._button(data_actions, "打开日志", self.open_logs).pack(side=LEFT, padx=(9, 0))

        footer = Frame(shell, background=BACKGROUND)
        footer.pack(fill=X, pady=(17, 0))
        self._label(footer, variable=self.footer_text, color="#4e6875", size=9).pack(side=LEFT)

    def _background(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        def runner() -> None:
            try:
                result: Any = (True, work())
            except Exception as exc:
                result = (False, exc)
            self.root.after(0, lambda: done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _local_snapshot(self) -> dict[str, Any]:
        ready: dict[str, Any] | None = None
        health: dict[str, Any] | None = None
        providers: list[Any] | None = None
        try:
            payload = _http_json(f"{self.base_url}/ready", timeout=3)
            ready = payload if isinstance(payload, dict) else None
        except (OSError, ValueError, urllib.error.URLError):
            pass
        owner = _listener_owner(self.port)
        if ready:
            try:
                payload = _http_json(f"{self.base_url}/health", timeout=8)
                health = payload if isinstance(payload, dict) else None
                payload = _http_json(f"{self.base_url}/api/providers/health", timeout=30)
                providers = payload if isinstance(payload, list) else None
            except (OSError, ValueError, urllib.error.URLError):
                pass
        return {
            "ready": ready,
            "owner": owner,
            "expected": _is_expected_server(owner),
            "health": health,
            "providers": providers,
            "database": self.data_dir / "database" / "stock_video.db",
        }

    def refresh_all(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.refresh_button.configure(state=DISABLED)
        self.footer_text.set("正在检查端口、后台、数据目录和行情源…")

        def done(result: tuple[bool, Any]) -> None:
            self.busy = False
            self.refresh_button.configure(state=NORMAL)
            ok, value = result
            if not ok:
                self.footer_text.set(f"检查失败：{value}")
                return
            self._apply_snapshot(value)
            self.check_updates()

        self._background(self._local_snapshot, done)

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        ready = snapshot["ready"]
        owner = snapshot["owner"]
        expected = snapshot["expected"]
        database: Path = snapshot["database"]
        if ready:
            self.service_title.set("后台服务已就绪")
            self.service_title_label.configure(foreground=GREEN)
            self.service_detail.set(
                f"HTTP 200  ·  PID {owner.pid if owner else '未知'}  ·  端口 {self.port}"
            )
            self.service_button.configure(text="重启后台服务")
            self.open_button.configure(state=NORMAL)
            self.version_text.set(f"本机版本  ·  v{ready.get('version', __version__)}")
        elif owner:
            self.service_title.set("端口被其他进程占用" if not expected else "后台尚未就绪")
            self.service_title_label.configure(foreground=RED if not expected else YELLOW)
            self.service_detail.set(f"PID {owner.pid} 正在监听端口 {self.port}")
            self.service_button.configure(text="重新检查")
            self.open_button.configure(state=DISABLED)
        else:
            self.service_title.set("后台服务尚未启动")
            self.service_title_label.configure(foreground=TEXT)
            self.service_detail.set(f"端口 {self.port} 当前空闲")
            self.service_button.configure(text="启动后台服务")
            self.open_button.configure(state=DISABLED)

        if database.is_file():
            size_mb = database.stat().st_size / 1024**2
            self.data_title.set("原有数据已连接")
            self.data_title_label.configure(foreground=GREEN)
            self.data_detail.set(f"{database}  ·  {size_mb:.1f} MB")
        else:
            self.data_title.set("数据目录已准备")
            self.data_title_label.configure(foreground=YELLOW)
            self.data_detail.set(f"首次运行将创建数据库：{database}")

        providers = snapshot["providers"] or []
        provider_ok = any(item.get("available") for item in providers if isinstance(item, dict))
        self.provider_text.set("行情数据源  ·  正常" if provider_ok else "行情数据源  ·  未就绪")
        self.provider_label.configure(foreground=GREEN if provider_ok else MUTED)
        components = (snapshot["health"] or {}).get("components", [])
        required = {"node", "remotion", "ffmpeg"}
        available = {
            item.get("name")
            for item in components
            if isinstance(item, dict) and item.get("available")
        }
        media_ok = required.issubset(available)
        self.media_text.set("媒体运行库  ·  正常" if media_ok else "媒体运行库  ·  未就绪")
        self.media_label.configure(foreground=GREEN if media_ok else MUTED)
        self.last_check_text.set("本机状态刚刚更新")
        self.footer_text.set("所有核心检查已完成，可以进入工作台" if ready else "请先启动后台服务")

    def start_or_restart(self) -> None:
        owner = _listener_owner(self.port)
        if owner and not _is_expected_server(owner):
            messagebox.showerror("端口冲突", f"端口 {self.port} 被其他程序占用，未执行停止操作。")
            return
        if owner and _is_expected_server(owner):
            try:
                owner.terminate()
                owner.wait(timeout=15)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass
        from stock_video_generator.desktop import _start_server, _wait_for_server

        self.service_title.set("正在启动后台服务…")
        self.service_title_label.configure(foreground=YELLOW)
        self.open_button.configure(state=DISABLED)
        self.server_process = _start_server(self.runtime_dir, self.log_dir, self.port)

        def work() -> bool:
            assert self.server_process is not None
            return _wait_for_server(self.server_process, self.port)

        def done(result: tuple[bool, Any]) -> None:
            if result[0] and result[1]:
                self.refresh_all()
            else:
                self.service_title.set("后台服务启动失败")
                self.service_title_label.configure(foreground=RED)
                self.service_detail.set(f"请查看日志：{self.log_dir}")

        self._background(work, done)

    def open_workbench(self) -> None:
        try:
            _http_json(f"{self.base_url}/ready", timeout=3)
        except Exception:
            messagebox.showwarning("后台未就绪", "请先启动或重新检查后台服务。")
            self.refresh_all()
            return
        webbrowser.open(self.base_url)

    def open_logs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer.exe", str(self.log_dir)])

    def _check_update(self) -> tuple[Any, Any | None, str]:
        from velopack import GithubSource, UpdateManager

        from stock_video_generator.desktop import _update_repo_url

        repo_url = _update_repo_url(self.runtime_dir)
        if not repo_url:
            return None, None, "未配置更新仓库"
        manager = UpdateManager(GithubSource(repo_url))
        if manager.get_is_portable():
            return manager, None, "当前为开发或便携运行模式"
        update = manager.check_for_updates()
        current = manager.get_current_version()
        return manager, update, current

    def check_updates(self) -> None:
        if self.check_update_button.cget("state") == DISABLED:
            return
        self.check_update_button.configure(state=DISABLED, text="正在检查…")
        self.update_button.configure(state=DISABLED)
        self.update_title.set("正在检查更新")
        self.update_detail.set("连接 GitHub 正式发布仓库…")

        def done(result: tuple[bool, Any]) -> None:
            self.check_update_button.configure(state=NORMAL, text="↻  检查更新")
            ok, value = result
            if not ok:
                self.update_title.set("暂时无法检查更新")
                self.update_detail.set(str(value))
                self._set_update_colors(YELLOW)
                return
            manager, update, current = value
            self.update_manager = manager
            self.update_info = update
            if update is None:
                self.update_title.set("当前已是最新版本")
                self.update_detail.set(f"本机 {current}  ·  没有可用更新")
                self._set_update_colors(GREEN)
                return
            target = update.TargetFullRelease.Version
            self.update_title.set(f"发现新版本 v{target}")
            self.update_detail.set(f"当前 {current}  ·  点击后自动下载、安装并重启")
            self._set_update_colors(BLUE)
            self.update_button.configure(state=NORMAL)

        self._background(self._check_update, done)

    def _set_update_colors(self, color: str) -> None:
        background = {GREEN: "#102920", YELLOW: "#2b2215", BLUE: "#11263a"}[color]
        border = {GREEN: "#1d6049", YELLOW: "#6e5422", BLUE: "#245b82"}[color]
        self.update_panel.configure(background=background, highlightbackground=border)
        for widget in (self.update_title_label, self.update_detail_label):
            widget.configure(background=background)

    def install_update(self) -> None:
        if self.update_manager is None or self.update_info is None:
            return
        if not messagebox.askyesno("确认更新", "下载并安装新版本吗？用户数据不会被覆盖。"):
            return
        self.update_button.configure(state=DISABLED, text="正在下载…")
        self.update_title.set("正在下载更新")

        def work() -> None:
            self.update_manager.download_updates(self.update_info)

        def done(result: tuple[bool, Any]) -> None:
            if not result[0]:
                self.update_button.configure(state=NORMAL, text="一键更新")
                self.update_title.set("更新下载失败")
                self.update_detail.set(str(result[1]))
                return
            owner = _listener_owner(self.port)
            if owner and _is_expected_server(owner):
                try:
                    owner.terminate()
                    owner.wait(timeout=20)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                    pass
            self.update_title.set("正在安装并重启")
            self.root.update_idletasks()
            self.update_manager.apply_updates_and_restart(self.update_info)
            self.root.destroy()

        self._background(work, done)

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_launcher_gui(runtime_dir: Path, log_dir: Path, port: int) -> int:
    try:
        return LauncherWindow(runtime_dir, log_dir, port).run()
    except Exception as exc:
        messagebox.showerror("启动中心错误", f"GUI 无法启动：{exc}\n\n请查看日志：{log_dir}")
        return 1
