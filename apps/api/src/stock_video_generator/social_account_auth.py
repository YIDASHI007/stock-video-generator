from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from stock_video_generator.config import Settings

SocialPlatform = Literal["douyin", "xiaohongshu", "wechat_channels"]


class AccountAuthError(RuntimeError):
    pass


class AccountAuthDependencyError(AccountAuthError):
    pass


class AccountLoginTimeout(AccountAuthError):
    pass


class AccountAlreadyLoggedIn(AccountAuthError):
    pass


@dataclass(frozen=True)
class PlatformAuthSpec:
    key: SocialPlatform
    label: str
    entry_url: str
    success_url_fragments: tuple[str, ...]
    login_markers: tuple[str, ...]
    qr_selectors: tuple[str, ...]
    qr_tab_labels: tuple[str, ...] = ()
    qr_switch_selectors: tuple[str, ...] = ()
    qr_retry_labels: tuple[str, ...] = ()
    qr_panel_markers: tuple[str, ...] = ()


PLATFORM_AUTH_SPECS: dict[SocialPlatform, PlatformAuthSpec] = {
    "douyin": PlatformAuthSpec(
        key="douyin",
        label="抖音",
        entry_url="https://creator.douyin.com/",
        success_url_fragments=("creator.douyin.com/creator-micro/",),
        login_markers=("扫码登录", "手机号登录", "二维码失效"),
        qr_selectors=(
            'div#animate_qrcode_container img[src^="data:image"]',
            'div[class*="animate_qrcode_container"] img[src^="data:image"]',
            'div[class*="scan_qrcode_login_content"] img[src^="data:image"]',
            'img[aria-label="二维码"]',
            'img[class*="qrcode"]',
        ),
        qr_tab_labels=("扫码登录",),
    ),
    "xiaohongshu": PlatformAuthSpec(
        key="xiaohongshu",
        label="小红书",
        entry_url="https://creator.xiaohongshu.com/login",
        success_url_fragments=("creator.xiaohongshu.com",),
        login_markers=("扫码登录", "手机扫码登录", "验证码登录", "APP扫一扫登录"),
        qr_selectors=(
            ".login-container .qrcode-img",
            ".login-box-container img",
            'div[class*="login-box"] img[src^="data:image"]',
            'div[class*="qrcode"] img',
            'img[class*="qrcode"]',
            'img[src^="data:image/"]',
        ),
        qr_tab_labels=("扫码登录", "扫一扫登录"),
        qr_switch_selectors=("img.css-wemwzq",),
        qr_panel_markers=("APP扫一扫登录",),
    ),
    "wechat_channels": PlatformAuthSpec(
        key="wechat_channels",
        label="微信视频号",
        entry_url="https://channels.weixin.qq.com/",
        success_url_fragments=("channels.weixin.qq.com/platform",),
        login_markers=("微信扫码登录", "扫码登录", "请使用微信扫码", "二维码已过期"),
        qr_selectors=(
            "div.login-qrcode-wrap img.qrcode",
            "div.qrcode-wrap img.qrcode",
            "img.qrcode",
            "img.js_qrcode_img",
            'img[src*="/connect/qrcode/"]',
            'img[src^="data:image/"]',
        ),
        qr_retry_labels=("加载失败，点击重试", "点击重试"),
    ),
}


@dataclass(frozen=True)
class AccountAuthResult:
    logged_in: bool
    screenshot_path: str
    dom_snapshot_path: str


QRReadyCallback = Callable[[Path], Any]
LoginProgressCallback = Callable[[str, str], Any]


class SocialAccountAuthenticator:
    """Persistent-browser authentication shared by all supported social platforms."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    async def _capture(page: Any, directory: Path, name: str) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        screenshot = directory / f"{name}.png"
        dom = directory / f"{name}.html"
        await page.screenshot(path=str(screenshot), full_page=True)
        dom.write_text(await page.content(), encoding="utf-8")
        return screenshot, dom

    @staticmethod
    async def _visible(locator: Any) -> bool:
        try:
            return bool(await locator.count()) and await locator.is_visible()
        except Exception:
            return False

    @classmethod
    async def _has_qr_surface(cls, page: Any, spec: PlatformAuthSpec) -> bool:
        for frame in page.frames:
            for selector in spec.qr_selectors:
                matches = frame.locator(selector)
                for index in range(min(await matches.count(), 12)):
                    locator = matches.nth(index)
                    if not await cls._visible(locator):
                        continue
                    try:
                        box = await locator.bounding_box()
                    except Exception:
                        box = None
                    if box is not None and min(box["width"], box["height"]) >= 80:
                        return True
        return False

    @classmethod
    async def _is_logged_in(cls, page: Any, spec: PlatformAuthSpec) -> bool:
        url = page.url.lower()
        return "login" not in url and any(
            fragment in url for fragment in spec.success_url_fragments
        )

    @classmethod
    async def _open_qr_panel(cls, page: Any, spec: PlatformAuthSpec) -> None:
        if await cls._has_qr_surface(page, spec):
            return
        for marker in spec.qr_panel_markers:
            if await cls._visible(page.get_by_text(marker, exact=False).first):
                return
        for label in spec.qr_tab_labels:
            locator = page.get_by_text(label, exact=False).first
            if not await cls._visible(locator):
                continue
            try:
                await locator.click(timeout=3000)
                await page.wait_for_timeout(500)
            except Exception:
                continue
            if await cls._has_qr_surface(page, spec):
                return
        for selector in spec.qr_switch_selectors:
            locator = page.locator(selector).first
            if not await cls._visible(locator):
                continue
            try:
                await locator.click(timeout=3000)
                await page.wait_for_timeout(500)
            except Exception:
                continue
            if await cls._has_qr_surface(page, spec):
                return
        for label in spec.qr_retry_labels:
            locator = page.get_by_text(label, exact=False).first
            if not await cls._visible(locator):
                continue
            try:
                await locator.click(timeout=3000)
                await page.wait_for_timeout(1000)
            except Exception:
                continue

    @classmethod
    async def _find_qr_locator(
        cls,
        page: Any,
        spec: PlatformAuthSpec,
        *,
        timeout_seconds: int = 45,
    ) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            if await cls._is_logged_in(page, spec):
                raise AccountAlreadyLoggedIn()
            await cls._open_qr_panel(page, spec)
            for frame in page.frames:
                for selector in spec.qr_selectors:
                    matches = frame.locator(selector)
                    for index in range(min(await matches.count(), 12)):
                        locator = matches.nth(index)
                        if not await cls._visible(locator):
                            continue
                        try:
                            box = await locator.bounding_box()
                        except Exception:
                            box = None
                        if box is None or min(box["width"], box["height"]) < 80:
                            continue
                        return locator
            await page.wait_for_timeout(500)
        raise AccountAuthError(f"未能从{spec.label}官方页面提取登录二维码，页面可能已改版")

    @classmethod
    async def _save_qr_code(
        cls,
        page: Any,
        spec: PlatformAuthSpec,
        target: Path,
    ) -> Path:
        locator = await cls._find_qr_locator(page, spec)
        target.parent.mkdir(parents=True, exist_ok=True)
        await locator.screenshot(path=str(target), type="png")
        return target

    @classmethod
    async def _refresh_expired_qr(cls, page: Any) -> bool:
        for text in ("二维码失效", "二维码已过期", "点击刷新", "网络不可用"):
            locator = page.get_by_text(text, exact=False).first
            if not await cls._visible(locator):
                continue
            try:
                await locator.click(timeout=3000)
                await page.wait_for_timeout(800)
                return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _emit(callback: Callable[..., Any] | None, *args: Any) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def _open_context(self, profile_dir: Path, *, headless: bool) -> Any:
        try:
            from patchright.async_api import async_playwright
        except ImportError as exc:
            raise AccountAuthDependencyError("缺少 patchright，无法检查账号登录状态") from exc

        playwright = await async_playwright().start()
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel=self.settings.publish_browser_channel,
                args=["--no-sandbox"],
            )
        except Exception:
            await playwright.stop()
            raise
        return playwright, context

    async def login(
        self,
        platform: SocialPlatform,
        profile_dir: Path,
        evidence_dir: Path,
        *,
        timeout_seconds: int = 300,
        on_qr_ready: QRReadyCallback | None = None,
        on_progress: LoginProgressCallback | None = None,
        show_browser: bool = False,
    ) -> AccountAuthResult:
        spec = PLATFORM_AUTH_SPECS[platform]
        profile_dir.mkdir(parents=True, exist_ok=True)
        await self._emit(on_progress, "preparing_qr", f"正在启动{spec.label}安全登录环境")
        playwright, context = await self._open_context(
            profile_dir,
            headless=not show_browser,
        )
        await self._emit(on_progress, "preparing_qr", f"正在打开{spec.label}官方登录页面")
        page = context.pages[0] if context.pages else await context.new_page()
        qr_path = evidence_dir / "login-qrcode.png"
        try:
            await page.goto(
                spec.entry_url,
                wait_until="domcontentloaded",
                timeout=self.settings.publish_step_timeout_seconds * 1000,
            )
            await self._emit(on_progress, "preparing_qr", f"正在读取{spec.label}登录状态")
            if await self._is_logged_in(page, spec):
                await self._emit(
                    on_progress,
                    "logged_in",
                    f"{spec.label}登录成功，正在保存本机会话",
                )
                return AccountAuthResult(True, "", "")

            try:
                await self._save_qr_code(page, spec, qr_path)
            except AccountAlreadyLoggedIn:
                await self._emit(
                    on_progress,
                    "logged_in",
                    f"{spec.label}登录成功，正在保存本机会话",
                )
                return AccountAuthResult(True, "", "")
            await self._emit(on_qr_ready, qr_path)
            await self._emit(on_progress, "waiting_scan", f"请使用手机扫码登录{spec.label}")

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            next_qr_refresh = loop.time() + 15
            while loop.time() < deadline:
                await page.wait_for_timeout(1500)
                if await self._is_logged_in(page, spec):
                    await self._emit(
                        on_progress,
                        "logged_in",
                        f"{spec.label}登录成功，正在保存本机会话",
                    )
                    return AccountAuthResult(True, "", "")
                scanned = False
                for text in ("已扫码", "请在手机上确认", "需在手机上进行确认"):
                    if await self._visible(page.get_by_text(text, exact=False).first):
                        scanned = True
                        break
                if scanned:
                    await self._emit(on_progress, "scanned", "已扫码，请在手机端确认登录")
                if loop.time() >= next_qr_refresh:
                    refreshed = await self._refresh_expired_qr(page)
                    try:
                        await self._save_qr_code(page, spec, qr_path)
                        await self._emit(on_qr_ready, qr_path)
                    except AccountAuthError:
                        if refreshed:
                            raise
                    next_qr_refresh = loop.time() + 15
            screenshot, _ = await self._capture(page, evidence_dir, "login-timeout")
            raise AccountLoginTimeout(f"等待{spec.label}扫码登录超时；最后界面：{screenshot}")
        finally:
            try:
                await asyncio.wait_for(context.close(), timeout=8)
            except TimeoutError:
                pass
            try:
                await asyncio.wait_for(playwright.stop(), timeout=5)
            except TimeoutError:
                pass
            qr_path.unlink(missing_ok=True)

    async def check(
        self,
        platform: SocialPlatform,
        profile_dir: Path,
        evidence_dir: Path,
    ) -> AccountAuthResult:
        spec = PLATFORM_AUTH_SPECS[platform]
        if not profile_dir.is_dir() or not any(profile_dir.iterdir()):
            return AccountAuthResult(False, "", "")
        playwright, context = await self._open_context(profile_dir, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(
                spec.entry_url,
                wait_until="domcontentloaded",
                timeout=self.settings.publish_step_timeout_seconds * 1000,
            )
            await page.wait_for_timeout(1200)
            logged_in = await self._is_logged_in(page, spec)
            name = "check-online" if logged_in else "check-offline"
            screenshot, dom = await self._capture(page, evidence_dir, name)
            return AccountAuthResult(logged_in, str(screenshot), str(dom))
        finally:
            await context.close()
            await playwright.stop()

    async def export_cookies(
        self,
        platform: SocialPlatform,
        profile_dir: Path,
    ) -> list[dict[str, object]]:
        """Read platform cookies through the saved browser profile without exposing them to UI."""
        if platform != "douyin":
            raise ValueError("仅抖音账号支持同步抓取凭证")
        if not profile_dir.is_dir() or not any(profile_dir.iterdir()):
            raise ValueError("账号浏览器会话不存在，请先扫码登录")
        playwright, context = await self._open_context(profile_dir, headless=True)
        try:
            cookies = await context.cookies(["https://www.douyin.com", "https://creator.douyin.com"])
            return [
                cookie
                for cookie in cookies
                if str(cookie.get("domain") or "").lstrip(".").endswith("douyin.com")
                and cookie.get("name")
                and cookie.get("value")
            ]
        finally:
            await context.close()
            await playwright.stop()
