from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

APP_NAME = "StockVideoGenerator"
APP_TITLE = "股票回测视频生成器"
APP_DATA_ROOT_NAME = "StockVideoGeneratorData"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8877
DEFAULT_NO_PROXY = (
    "127.0.0.1",
    "localhost",
    "::1",
    ".eastmoney.com",
    ".sinajs.cn",
    ".sina.com.cn",
)
_NULL_STREAMS: list[Any] = []


def _resolve_node_executable(runtime_dir: Path) -> Path | None:
    configured = os.environ.get("NODE_EXECUTABLE")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return configured_path.resolve()
        discovered = shutil.which(configured)
        if discovered:
            return Path(discovered).resolve()

    candidates = [runtime_dir / "runtime" / "node" / "node.exe"]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / APP_NAME
            / "current"
            / "runtime"
            / "node"
            / "node.exe"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    discovered = shutil.which("node")
    return Path(discovered).resolve() if discovered else None


def _runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


def _local_app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_DATA_ROOT_NAME


def _load_user_settings(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "launcher.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _merge_no_proxy(existing: str | None) -> str:
    values = [value.strip() for value in (existing or "").split(",") if value.strip()]
    known = {value.lower() for value in values}
    for value in DEFAULT_NO_PROXY:
        if value.lower() not in known:
            values.append(value)
            known.add(value.lower())
    return ",".join(values)


def _configure_proxy_environment(user_settings: dict[str, Any]) -> None:
    """Bridge the Windows user proxy into libraries that only inspect env vars.

    Chinese market-data hosts stay direct because some local proxy routes cannot
    reach Eastmoney, while Yahoo can still use the configured Windows proxy.
    ``use_system_proxy: false`` in launcher.json disables automatic discovery.
    """
    if user_settings.get("use_system_proxy", True) is False:
        return

    configured = user_settings.get("proxy_url")
    proxies: dict[str, str] = {}
    if isinstance(configured, str) and configured.strip():
        proxies = {"http": configured.strip(), "https": configured.strip()}
    elif not any(
        os.environ.get(name)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    ):
        proxies = {
            key: value
            for key, value in urllib.request.getproxies().items()
            if key in {"http", "https"} and isinstance(value, str) and value
        }

    for scheme in ("http", "https"):
        value = proxies.get(scheme)
        if value:
            os.environ.setdefault(f"{scheme.upper()}_PROXY", value)
            os.environ.setdefault(f"{scheme}_proxy", value)

    no_proxy = _merge_no_proxy(
        str(user_settings.get("no_proxy") or os.environ.get("NO_PROXY") or "")
    )
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy


def _configure_environment() -> tuple[Path, Path, int]:
    runtime_dir = _runtime_dir()
    config_dir = _local_app_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    user_settings = _load_user_settings(config_dir)
    _configure_proxy_environment(user_settings)

    data_dir = Path(
        os.environ.get("APP_DATA_DIR")
        or user_settings.get("data_dir")
        or config_dir / "UserData"
    ).expanduser().resolve()
    log_dir = Path(
        os.environ.get("APP_LOG_DIR")
        or user_settings.get("log_dir")
        or config_dir / "Logs"
    ).expanduser().resolve()
    port = int(os.environ.get("APP_PORT") or user_settings.get("port") or DEFAULT_PORT)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    os.environ["APP_ENV"] = "production"
    os.environ["APP_HOST"] = DEFAULT_HOST
    os.environ["APP_PORT"] = str(port)
    os.environ["APP_RUNTIME_DIR"] = str(runtime_dir)
    os.environ["APP_DATA_DIR"] = str(data_dir)
    os.environ["APP_LOG_DIR"] = str(log_dir)
    os.environ["APP_WEB_DIST_DIR"] = str(runtime_dir / "apps" / "web" / "dist")
    node_executable = _resolve_node_executable(runtime_dir)
    if node_executable is not None:
        os.environ["NODE_EXECUTABLE"] = str(node_executable)
    else:
        os.environ.pop("NODE_EXECUTABLE", None)
    return runtime_dir, log_dir, port


def _log(log_dir: Path, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with (log_dir / "desktop-launcher.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _bootstrap_log(message: str) -> None:
    if not getattr(sys, "frozen", False):
        return
    try:
        directory = _local_app_dir()
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (directory / "bootstrap.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _ensure_standard_streams() -> None:
    if not getattr(sys, "frozen", False):
        return
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is not None:
            continue
        stream = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        _NULL_STREAMS.append(stream)
        setattr(sys, name, stream)


def _message(text: str, *, error: bool = False, question: bool = False) -> bool:
    try:
        import ctypes

        flags = 0x10 if error else 0x40
        if question:
            flags = 0x24
        result = ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, flags)
        return result == 6
    except Exception:
        return False


def _health_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{port}/ready"


def _app_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{port}/"


def _is_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(_health_url(port), timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _self_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "stock_video_generator.desktop", *arguments]


def _start_server(runtime_dir: Path, log_dir: Path, port: int) -> subprocess.Popen[bytes]:
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    stdout = (log_dir / "desktop-api.stdout.log").open("ab")
    stderr = (log_dir / "desktop-api.stderr.log").open("ab")
    process = subprocess.Popen(
        _self_command("--serve", "--port", str(port)),
        cwd=runtime_dir,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        creationflags=creation_flags,
    )
    stdout.close()
    stderr.close()
    return process


def _wait_for_server(process: subprocess.Popen[bytes], port: int, timeout: float = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _is_ready(port):
            return True
        time.sleep(0.5)
    return False


def _update_repo_url(runtime_dir: Path) -> str | None:
    configured = os.environ.get("STOCK_VIDEO_UPDATE_URL")
    if configured:
        return configured.strip() or None
    path = runtime_dir / "resources" / "update.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("github_repo_url") if isinstance(payload, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _check_and_apply_update(
    runtime_dir: Path,
    log_dir: Path,
    process: subprocess.Popen[bytes],
) -> bool:
    repo_url = _update_repo_url(runtime_dir)
    if not repo_url:
        return False
    try:
        from velopack import GithubSource, UpdateManager

        manager = UpdateManager(GithubSource(repo_url))
        if manager.get_is_portable():
            return False
        update = manager.check_for_updates()
        if update is None:
            return False
        if not _message("发现新版本。现在下载并自动安装吗？", question=True):
            return False
        manager.download_updates(update)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
        manager.apply_updates_and_restart(update)
        return True
    except Exception as exc:
        _log(log_dir, f"Update check failed: {exc!r}")
        return False


def _monitor_updates(
    runtime_dir: Path,
    log_dir: Path,
    process: subprocess.Popen[bytes],
) -> None:
    if _check_and_apply_update(runtime_dir, log_dir, process):
        return
    while process.poll() is None:
        time.sleep(6 * 60 * 60)
        if _check_and_apply_update(runtime_dir, log_dir, process):
            return


def _run_server(port: int) -> int:
    _bootstrap_log("Importing uvicorn.")
    import uvicorn

    if getattr(sys, "frozen", False):
        startup_modules = (
            "fastapi",
            "sqlalchemy",
            "stock_video_generator.api_models",
            "stock_video_generator.config",
            "stock_video_generator.database",
            "stock_video_generator.errors",
            "stock_video_generator.jobs",
            "stock_video_generator.logging_config",
            "stock_video_generator.market_data",
            "stock_video_generator.models",
            "stock_video_generator.output_retention",
            "stock_video_generator.pipeline",
            "stock_video_generator.publish_batches",
            "stock_video_generator.publish_manager",
            "stock_video_generator.publishing",
            "stock_video_generator.scripting",
            "stock_video_generator.thumbnails",
            "stock_video_generator.topics",
            "stock_video_generator.tts",
            "stock_video_generator.universe",
            "stock_video_generator.visualization",
        )
        for module_name in startup_modules:
            _bootstrap_log(f"Importing {module_name}.")
            importlib.import_module(module_name)

    _bootstrap_log("Importing FastAPI application.")
    from stock_video_generator.main import app

    _bootstrap_log("FastAPI application imported; entering uvicorn.run().")
    uvicorn.run(
        app,
        host=DEFAULT_HOST,
        port=port,
        reload=False,
        access_log=False,
        log_config=None,
    )
    return 0


def _run_launcher(runtime_dir: Path, log_dir: Path, port: int) -> int:
    from stock_video_generator.launcher_gui import run_launcher_gui

    _log(log_dir, "Opening desktop launch center.")
    return run_launcher_gui(runtime_dir, log_dir, port)


def main() -> int:
    _ensure_standard_streams()
    _bootstrap_log(f"Starting with arguments: {sys.argv[1:]!r}")
    is_server_process = "--serve" in sys.argv[1:]
    if not is_server_process:
        try:
            from velopack import App

            App().set_auto_apply_on_startup(True).run()
        except Exception:
            # Development and unpackaged builds run in portable mode.
            pass

    _bootstrap_log("Configuring runtime environment.")
    runtime_dir, log_dir, default_port = _configure_environment()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=default_port)
    arguments, _ = parser.parse_known_args()
    if arguments.serve:
        _bootstrap_log(f"Starting API service on port {arguments.port}.")
        return _run_server(arguments.port)
    _bootstrap_log("Starting desktop launcher.")
    return _run_launcher(runtime_dir, log_dir, arguments.port)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        _bootstrap_log(traceback.format_exc())
        raise
