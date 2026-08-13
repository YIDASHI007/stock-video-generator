"""Deterministic Douyin Creator Center publisher with bounded agent fallback.

The DOM workflow is derived from the MIT-licensed
``dreammis/social-auto-upload`` Douyin uploader, but is implemented behind this
project's own state machine, manifest, approval gate, and evidence recorder.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import socket
import struct
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stock_video_generator.config import Settings
from stock_video_generator.database import PublishStage
from stock_video_generator.publishing import PublishManifest

StageCallback = Callable[[PublishStage, float], Awaitable[None]]

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE_URL_GLOB = "**/creator-micro/content/manage**"


class PublishBrowserError(RuntimeError):
    retryable = True


class PublishDependencyError(PublishBrowserError):
    retryable = False


class PublishNeedsLogin(PublishBrowserError):
    retryable = False


class PublishNeedsSms(PublishBrowserError):
    retryable = False


class PublishNeedsHuman(PublishBrowserError):
    retryable = False


@dataclass
class PublishBrowserResult:
    stage: PublishStage
    screenshot_path: str
    dom_snapshot_path: str
    action_log_path: str
    item_id: str | None = None
    published_url: str | None = None
    agent_fallback_count: int = 0


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是有效PNG文件：{path}")
    return struct.unpack(">II", header[16:24])


def validate_publish_media(manifest: PublishManifest) -> None:
    video = Path(manifest.media.video_path)
    portrait = Path(manifest.media.cover_portrait_path)
    landscape = Path(manifest.media.cover_landscape_path)
    for path, label in (
        (video, "视频"),
        (portrait, "竖封面"),
        (landscape, "横封面"),
    ):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"{label}文件不存在或为空：{path}")
    if _png_size(portrait) != (1080, 1440):
        raise ValueError("竖封面必须为1080×1440（3:4）")
    if _png_size(landscape) != (1440, 1080):
        raise ValueError("横封面必须为1440×1080（4:3）")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AgentFallback:
    """Invoke a Stagehand sidecar for one bounded, non-publishing UI goal."""

    def __init__(self, settings: Settings, evidence_dir: Path) -> None:
        configured = settings.publish_agent_command
        bundled_entry = (
            settings.runtime_dir / "apps" / "publisher-agent" / "dist" / "index.js"
        )
        source_entry = (
            settings.runtime_dir / "apps" / "publisher-agent" / "src" / "index.ts"
        )
        tsx_entry = settings.runtime_dir / "node_modules" / "tsx" / "dist" / "cli.mjs"
        node = settings.resolve_node_executable()
        if configured:
            self.command_args = shlex.split(configured, posix=os.name != "nt")
        elif (
            settings.env == "development"
            and node
            and source_entry.is_file()
            and tsx_entry.is_file()
        ):
            self.command_args = [node, str(tsx_entry), str(source_entry)]
        elif node and bundled_entry.is_file():
            self.command_args = [node, str(bundled_entry)]
        elif node and source_entry.is_file() and tsx_entry.is_file():
            self.command_args = [node, str(tsx_entry), str(source_entry)]
        else:
            self.command_args = []
        self.environment = os.environ.copy()
        self.environment["PUBLISH_AGENT_MODEL"] = settings.publish_agent_model
        if settings.openai_api_key is not None:
            self.environment["OPENAI_API_KEY"] = settings.openai_api_key.get_secret_value()
        self.timeout = settings.publish_step_timeout_seconds
        self.max_calls = settings.publish_max_agent_fallbacks
        self.evidence_dir = evidence_dir
        self.calls = 0

    async def try_recover(
        self,
        *,
        goal: str,
        current_stage: PublishStage,
        cdp_url: str,
        screenshot_path: Path,
        forbidden_actions: list[str] | None = None,
    ) -> bool:
        if not self.command_args or self.calls >= self.max_calls:
            return False
        self.calls += 1
        request_path = self.evidence_dir / f"agent-request-{self.calls}.json"
        result_path = self.evidence_dir / f"agent-result-{self.calls}.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "goal": goal,
                    "stage": current_stage,
                    "cdp_url": cdp_url,
                    "screenshot_path": str(screenshot_path),
                    "result_path": str(result_path),
                    "max_steps": 4,
                    "forbidden_actions": forbidden_actions
                    or ["点击发布", "提交短信验证码", "绕过登录或安全验证"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        args = [*self.command_args, str(request_path)]

        def run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                env=self.environment,
            )

        completed = await asyncio.to_thread(run)
        (self.evidence_dir / f"agent-process-{self.calls}.json").write_text(
            json.dumps(
                {
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-8000:],
                    "stderr": completed.stderr[-8000:],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if completed.returncode != 0 or not result_path.is_file():
            return False
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return result.get("success") is True


class DouyinBrowserPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _evidence(
        self,
        page: Any,
        directory: Path,
        name: str,
    ) -> tuple[Path, Path]:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
        screenshot = directory / f"{safe}.png"
        dom = directory / f"{safe}.html"
        await page.screenshot(path=str(screenshot), full_page=True)
        dom.write_text(await page.content(), encoding="utf-8")
        return screenshot, dom

    async def _login_required(self, page: Any) -> bool:
        for text in ("扫码登录", "手机号登录"):
            locator = page.get_by_text(text, exact=True).first
            if await locator.count():
                try:
                    if await locator.is_visible():
                        return True
                except Exception:
                    pass
        return "content/upload" not in page.url

    async def login(
        self,
        profile_dir: Path,
        evidence_dir: Path,
        *,
        timeout_seconds: int = 300,
    ) -> tuple[str, str]:
        """Open a visible persistent browser and wait for the user to finish login."""

        try:
            from patchright.async_api import async_playwright
        except ImportError as exc:
            raise PublishDependencyError("缺少 patchright，无法打开登录浏览器") from exc

        profile_dir.mkdir(parents=True, exist_ok=True)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                channel=self.settings.publish_browser_channel,
                args=["--no-sandbox"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(
                    UPLOAD_URL,
                    wait_until="domcontentloaded",
                    timeout=self.settings.publish_step_timeout_seconds * 1000,
                )
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout_seconds
                while loop.time() < deadline:
                    await page.wait_for_timeout(1500)
                    if not await self._login_required(page):
                        screenshot, dom = await self._evidence(page, evidence_dir, "login-success")
                        return str(screenshot), str(dom)
                screenshot, _ = await self._evidence(page, evidence_dir, "login-timeout")
                raise PublishNeedsLogin(f"等待扫码登录超时；最后界面已保存：{screenshot}")
            finally:
                await context.close()

    async def _image_input(self, container: Any) -> Any:
        inputs = container.locator('input[type="file"]')
        count = await inputs.count()
        custom_cover_inputs: list[tuple[bool, Any]] = []
        candidates: list[Any] = []
        for index in range(count):
            locator = inputs.nth(index)
            accept = (await locator.get_attribute("accept") or "").lower()
            if "image" not in accept and ".png" not in accept and ".jpg" not in accept:
                continue
            candidates.append(locator)
            context_text = await locator.evaluate(
                """element => {
                    let node = element.parentElement;
                    let text = "";
                    for (let depth = 0; node && depth < 5; depth += 1) {
                        text += ` ${node.textContent || ""}`;
                        node = node.parentElement;
                    }
                    return text;
                }"""
            )
            if "上传封面" in context_text and "生成参考图" not in context_text:
                classes = (await locator.get_attribute("class") or "").lower()
                custom_cover_inputs.append(("replace" in classes, locator))
        if custom_cover_inputs:
            # 初次上传必须使用普通输入框；*-replace 只适合已有自定义图时替换，
            # 对尚未上传的封面使用它会出现“上传成功”与“格式不支持”并存的假成功。
            custom_cover_inputs.sort(key=lambda item: item[0])
            return custom_cover_inputs[0][1]
        if not candidates:
            candidates = [inputs.nth(index) for index in range(count)]
        if not candidates:
            raise PublishBrowserError("封面弹窗中没有找到图片上传输入框")
        return candidates[0]

    async def _upload_cover_file(
        self,
        page: Any,
        modal: Any,
        file_path: str,
    ) -> None:
        # 同一弹窗同时存在 AI 参考图、初次自定义上传和 replace 三类隐藏
        # 输入框；_image_input 会按父区域文字与 class 选择初次自定义上传。
        upload = await self._image_input(modal)
        await upload.set_input_files(file_path)
        await page.wait_for_timeout(2500)

        format_error = page.get_by_text("不支持的图片格式", exact=False).last
        if await format_error.count():
            try:
                if await format_error.is_visible():
                    raise PublishBrowserError(
                        f"抖音拒绝了封面图片；文件已校验为PNG：{file_path}"
                    )
            except PublishBrowserError:
                raise
            except Exception:
                pass

    async def _set_cover(
        self,
        page: Any,
        modal: Any,
        *,
        tab_text: str,
        file_path: str,
        stage: PublishStage,
        progress: float,
        update: StageCallback,
        evidence_dir: Path,
        agent: AgentFallback,
        cdp_url: str,
    ) -> None:
        await update(stage, progress)
        try:
            tab = modal.get_by_text(tab_text, exact=True).first
            await tab.wait_for(
                state="visible", timeout=self.settings.publish_step_timeout_seconds * 1000
            )
            await tab.click()
            await page.wait_for_timeout(700)
            await self._upload_cover_file(page, modal, file_path)
        except Exception:
            screenshot, _ = await self._evidence(page, evidence_dir, f"{stage}-failed")
            recovered = await agent.try_recover(
                goal=f"在当前封面弹窗中打开“{tab_text}”并准备好自定义图片上传区域，不要点击完成",
                current_stage=stage,
                cdp_url=cdp_url,
                screenshot_path=screenshot,
            )
            if not recovered:
                raise
            await self._upload_cover_file(page, modal, file_path)
        await self._evidence(page, evidence_dir, f"{stage}-applied")

    async def execute(
        self,
        manifest: PublishManifest,
        profile_dir: Path,
        *,
        evidence_key: str,
        approved: bool,
        update: StageCallback,
    ) -> PublishBrowserResult:
        await update(PublishStage.VALIDATING_ARTIFACTS, 0.03)
        validate_publish_media(manifest)
        evidence_dir = (
            self.settings.data_dir
            / "publishes"
            / manifest.publish_id
            / "attempt-evidence"
            / evidence_key
        )
        evidence_dir.mkdir(parents=True, exist_ok=True)
        action_log: list[dict[str, object]] = []
        action_log_path = evidence_dir / "actions.json"
        cdp_port = _free_tcp_port()
        cdp_url = f"http://127.0.0.1:{cdp_port}"
        agent = AgentFallback(self.settings, evidence_dir)
        last_screenshot = evidence_dir / "not-started.png"
        last_dom = evidence_dir / "not-started.html"
        try:
            from patchright.async_api import async_playwright
        except ImportError as exc:
            raise PublishDependencyError(
                "缺少 patchright；请安装项目依赖并执行 patchright install chromium"
            ) from exc

        profile_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.settings.publish_headless,
                channel=self.settings.publish_browser_channel,
                args=[
                    "--no-sandbox",
                    f"--remote-debugging-port={cdp_port}",
                ],
            )
            context.set_default_timeout(self.settings.publish_step_timeout_seconds * 1000)
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await update(PublishStage.CHECKING_LOGIN, 0.06)
                await update(PublishStage.OPENING_UPLOAD_PAGE, 0.09)
                await page.goto(
                    UPLOAD_URL,
                    wait_until="domcontentloaded",
                    timeout=self.settings.publish_step_timeout_seconds * 1000,
                )
                await page.wait_for_timeout(1800)
                if await self._login_required(page):
                    last_screenshot, last_dom = await self._evidence(
                        page, evidence_dir, "needs-login"
                    )
                    raise PublishNeedsLogin("抖音登录已失效，请在浏览器中完成扫码登录")

                await update(PublishStage.UPLOADING_VIDEO, 0.13)
                upload_input = page.locator(
                    'input[type="file"][accept*="video"], '
                    'div[class^="container"] input[type="file"]'
                ).first
                await upload_input.wait_for(state="attached")
                await upload_input.set_input_files(manifest.media.video_path)
                action_log.append({"action": "upload_video", "path": manifest.media.video_path})

                await update(PublishStage.WAITING_TRANSCODE, 0.24)
                title_input = page.locator('input[placeholder*="填写作品标题"]').first
                await title_input.wait_for(
                    state="visible",
                    timeout=self.settings.publish_upload_timeout_seconds * 1000,
                )
                await update(PublishStage.FILLING_TITLE, 0.34)
                await title_input.fill(manifest.content.selected_title)
                if await title_input.input_value() != manifest.content.selected_title:
                    raise PublishBrowserError("标题回读校验失败")

                await update(PublishStage.FILLING_DESCRIPTION, 0.41)
                editor = page.locator('div.zone-container[contenteditable="true"]').first
                await editor.wait_for(state="visible")
                await editor.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Delete")
                await page.keyboard.insert_text(manifest.content.description)
                description_text = (await editor.inner_text()).strip()
                if manifest.content.description.splitlines()[0] not in description_text:
                    raise PublishBrowserError("作品简介回读校验失败")

                await update(PublishStage.ADDING_TOPICS, 0.47)
                for topic in manifest.content.topics:
                    await page.keyboard.insert_text(f" #{topic}")
                    await page.keyboard.press("Space")
                    await page.wait_for_timeout(150)
                await page.keyboard.press("Escape")

                await page.get_by_text("选择封面", exact=True).first.click(force=True)
                modal = page.locator("div.dy-creator-content-modal").first
                await modal.wait_for(state="visible")
                await self._set_cover(
                    page,
                    modal,
                    tab_text="设置横封面",
                    file_path=manifest.media.cover_landscape_path,
                    stage=PublishStage.SETTING_LANDSCAPE_COVER,
                    progress=0.54,
                    update=update,
                    evidence_dir=evidence_dir,
                    agent=agent,
                    cdp_url=cdp_url,
                )
                await self._set_cover(
                    page,
                    modal,
                    tab_text="设置竖封面",
                    file_path=manifest.media.cover_portrait_path,
                    stage=PublishStage.SETTING_PORTRAIT_COVER,
                    progress=0.62,
                    update=update,
                    evidence_dir=evidence_dir,
                    agent=agent,
                    cdp_url=cdp_url,
                )
                await modal.get_by_role("button", name="完成", exact=True).first.click()
                await modal.wait_for(state="detached")

                if manifest.content.collection:
                    await update(PublishStage.SETTING_COLLECTION, 0.68)
                    try:
                        collection_trigger = page.get_by_text("请选择合集", exact=True).first
                        if await collection_trigger.count():
                            await collection_trigger.click()
                            option = page.get_by_text(manifest.content.collection, exact=True).first
                            await option.wait_for(state="visible", timeout=5000)
                            await option.click()
                    except Exception as exc:
                        action_log.append({"warning": "collection_not_set", "detail": str(exc)})

                if manifest.content.declaration:
                    await update(PublishStage.SETTING_DECLARATION, 0.73)
                    trigger = page.get_by_text("请选择自主声明", exact=True).first
                    await trigger.click()
                    dialog = (
                        page.locator(".semi-modal-content")
                        .filter(has_text="对作品内容添加声明")
                        .first
                    )
                    await dialog.wait_for(state="visible")
                    option = (
                        dialog.locator(".semi-radio")
                        .filter(has_text=manifest.content.declaration)
                        .first
                    )
                    await option.click()
                    await dialog.get_by_role("button", name="确定").click()
                    await dialog.wait_for(state="hidden")

                await update(PublishStage.VALIDATING_PREVIEW, 0.8)
                last_screenshot, last_dom = await self._evidence(
                    page, evidence_dir, "ready-for-publish"
                )
                missing_cover_notice = page.get_by_text("横/竖双封面缺失", exact=False).first
                if await missing_cover_notice.count():
                    try:
                        if await missing_cover_notice.is_visible():
                            raise PublishBrowserError("发布页仍提示横/竖双封面缺失")
                    except PublishBrowserError:
                        raise
                    except Exception:
                        pass

                publish_button = page.get_by_role("button", name="发布", exact=True).first
                await publish_button.wait_for(state="visible")
                if manifest.mode == "dry_run":
                    await update(PublishStage.READY_FOR_PUBLISH, 0.9)
                    return PublishBrowserResult(
                        stage=PublishStage.READY_FOR_PUBLISH,
                        screenshot_path=str(last_screenshot),
                        dom_snapshot_path=str(last_dom),
                        action_log_path=str(action_log_path),
                        agent_fallback_count=agent.calls,
                    )
                if not approved:
                    raise PublishNeedsHuman("正式发布尚未经过人工授权")

                if manifest.mode == "scheduled":
                    if manifest.scheduled_at is None:
                        raise PublishNeedsHuman("定时发布缺少发布时间")
                    schedule = page.get_by_text("定时发布", exact=True).first
                    await schedule.click()
                    date_input = page.locator('.semi-input[placeholder="日期和时间"]').first
                    await date_input.fill(
                        manifest.scheduled_at.astimezone().strftime("%Y-%m-%d %H:%M")
                    )
                    await date_input.press("Enter")

                sms_input = page.locator(
                    'input[placeholder*="验证码"], input[placeholder*="短信"], input[type="tel"]'
                ).first
                if await sms_input.count() and await sms_input.is_visible():
                    raise PublishNeedsSms("发布前需要短信验证，请人工完成")

                await update(PublishStage.PUBLISHING, 0.92)
                await publish_button.click(force=True)
                await update(PublishStage.VERIFYING_RESULT, 0.96)
                await page.wait_for_url(
                    MANAGE_URL_GLOB,
                    timeout=self.settings.publish_step_timeout_seconds * 1000,
                )
                last_screenshot, last_dom = await self._evidence(page, evidence_dir, "published")
                return PublishBrowserResult(
                    stage=PublishStage.PUBLISHED,
                    screenshot_path=str(last_screenshot),
                    dom_snapshot_path=str(last_dom),
                    action_log_path=str(action_log_path),
                    published_url=page.url,
                    agent_fallback_count=agent.calls,
                )
            except Exception:
                if page:
                    try:
                        last_screenshot, last_dom = await self._evidence(
                            page, evidence_dir, "failure"
                        )
                    except Exception:
                        pass
                raise
            finally:
                action_log_path.write_text(
                    json.dumps(action_log, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                await context.close()
