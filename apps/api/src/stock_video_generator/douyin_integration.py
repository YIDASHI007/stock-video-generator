from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import shutil
import zipfile
from collections.abc import AsyncIterator
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, field_validator

from stock_video_generator.ai_models import AiModelService
from stock_video_generator.config import Settings

_MOJIBAKE_MARKERS = re.compile(r"[ÃÂäåæçèéð][\x80-\xBF\w]?")


def _looks_like_mojibake(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    suspicious = len(_MOJIBAKE_MARKERS.findall(value))
    chinese = len(re.findall(r"[\u3400-\u9fff]", value))
    return suspicious >= 3 and suspicious > chinese / 2


def _repair_work_transcript(work: dict[str, Any]) -> bool:
    transcript = work.get("transcript")
    raw = work.get("transcript_raw")
    if not (
        _looks_like_mojibake(transcript)
        and isinstance(raw, str)
        and raw.strip()
        and not _looks_like_mojibake(raw)
    ):
        return False
    work["transcript"] = raw.strip()
    if _looks_like_mojibake(work.get("transcript_edited")):
        work["transcript_edited"] = raw.strip()
    work["transcript_recovered_from_raw"] = True
    return True


class DouyinRemoteSettings(BaseModel):
    enabled: bool = False
    base_url: str = ""
    client_id: str = ""
    api_key: str | None = Field(default=None, repr=False)
    connect_timeout_seconds: int = Field(default=15, ge=3, le=120)
    job_timeout_seconds: int = Field(default=3600, ge=60, le=86_400)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        parsed = urlparse(value)
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in local_hosts
        ):
            raise ValueError("远程服务必须使用 HTTPS；仅本机测试允许 HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址格式无效")
        return value


class DouyinSettingsUpdate(BaseModel):
    enabled: bool
    base_url: str
    client_id: str
    api_key: str | None = None
    connect_timeout_seconds: int = Field(default=15, ge=3, le=120)
    job_timeout_seconds: int = Field(default=3600, ge=60, le=86_400)


class DouyinExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


class DouyinAccountResolveRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class DouyinAccountSyncRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)


class DouyinAccountBatchRequest(BaseModel):
    aweme_ids: list[str] = Field(min_length=1, max_length=50)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


class DouyinAccountAnalyzeRequest(BaseModel):
    sample_size: int = Field(default=50, ge=5, le=200)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(value: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        return value
    source = ctypes.create_string_buffer(value)
    in_blob = _DataBlob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if decrypt:
        ok = function(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    else:
        ok = function(
            ctypes.byref(in_blob),
            "StockVideoGenerator",
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
    if not ok:
        raise OSError("Windows 凭据加密操作失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _encode_secret(value: str) -> str:
    protected = _dpapi(value.encode("utf-8"), decrypt=False)
    return base64.b64encode(protected).decode("ascii")


def _decode_secret(value: str) -> str:
    protected = base64.b64decode(value)
    return _dpapi(protected, decrypt=True).decode("utf-8")


class DouyinIntegration:
    def __init__(self, settings: Settings, ai_models: AiModelService | None = None) -> None:
        self.settings = settings
        self.ai_models = ai_models or AiModelService(settings)
        self.root = settings.data_dir / "integrations" / "douyin"
        self.import_root = settings.data_dir / "imports" / "douyin"
        self.config_path = self.root / "settings.json"
        self.jobs_path = self.root / "jobs.json"
        self.accounts_path = self.root / "accounts.json"
        self.account_media_root = self.root / "account-media"
        self.root.mkdir(parents=True, exist_ok=True)
        self.import_root.mkdir(parents=True, exist_ok=True)
        self.account_media_root.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> DouyinRemoteSettings:
        if not self.config_path.is_file():
            return DouyinRemoteSettings()
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        encrypted = payload.pop("api_key_encrypted", None)
        payload["api_key"] = _decode_secret(encrypted) if encrypted else None
        return DouyinRemoteSettings.model_validate(payload)

    def public_settings(self) -> dict[str, Any]:
        configured = self.load_settings()
        payload = configured.model_dump(exclude={"api_key"})
        payload["api_key_configured"] = bool(configured.api_key)
        payload["api_key_hint"] = (
            f"••••••••{configured.api_key[-4:]}" if configured.api_key else None
        )
        return payload

    def save_settings(self, request: DouyinSettingsUpdate) -> dict[str, Any]:
        current = self.load_settings()
        api_key = request.api_key.strip() if request.api_key else current.api_key
        values = request.model_dump()
        values["api_key"] = api_key
        validated = DouyinRemoteSettings(**values)
        payload = validated.model_dump(exclude={"api_key"})
        payload["api_key_encrypted"] = _encode_secret(api_key) if api_key else None
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)
        return self.public_settings()

    def _configured(self) -> DouyinRemoteSettings:
        configured = self.load_settings()
        if not configured.enabled:
            raise RuntimeError("抖音提取服务尚未启用")
        if not configured.base_url or not configured.client_id or not configured.api_key:
            raise RuntimeError("抖音提取服务配置不完整")
        return configured

    def _headers(self, configured: DouyinRemoteSettings) -> dict[str, str]:
        return {
            "X-Client-ID": configured.client_id,
            "Authorization": f"Bearer {configured.api_key}",
            "User-Agent": "StockVideoGenerator-DouyinIntegration/0.1.13",
        }

    def _load_jobs(self) -> dict[str, dict[str, Any]]:
        if not self.jobs_path.is_file():
            return {}
        try:
            payload = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_jobs(self, payload: dict[str, dict[str, Any]]) -> None:
        temporary = self.jobs_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.jobs_path)

    def _load_account_cache(self) -> dict[str, dict[str, Any]]:
        if not self.accounts_path.is_file():
            return {}
        try:
            payload = json.loads(self.accounts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        repaired = False
        for account in payload.values():
            if not isinstance(account, dict):
                continue
            for work in account.get("works") or []:
                if isinstance(work, dict):
                    repaired = _repair_work_transcript(work) or repaired
        if repaired:
            self._save_account_cache(payload)
        return payload

    def _save_account_cache(self, payload: dict[str, dict[str, Any]]) -> None:
        temporary = self.accounts_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.accounts_path)

    def _cached_account(self, sec_uid: str) -> dict[str, Any] | None:
        value = self._load_account_cache().get(sec_uid)
        return dict(value) if isinstance(value, dict) else None

    def _cache_account(self, account: dict[str, Any]) -> dict[str, Any]:
        sec_uid = str(account.get("sec_uid") or "")
        if not sec_uid:
            raise ValueError("账号缺少唯一标识")
        records = self._load_account_cache()
        previous = records.get(sec_uid) or {}
        previous_works = {
            str(item.get("aweme_id") or ""): item for item in previous.get("works") or []
        }
        archived = json.loads(json.dumps(account, ensure_ascii=False))
        previous_methodology = (previous.get("portrait") or {}).get("methodology") or {}
        if previous_methodology.get("analysis_engine"):
            archived["portrait"] = previous["portrait"]
        elif "portrait" in previous and "portrait" not in archived:
            archived["portrait"] = previous["portrait"]
        if "skill_export" in previous:
            archived["skill_export"] = previous["skill_export"]
        archived["archive_updated_at"] = datetime.now(UTC).isoformat()
        archived["archive_storage"] = "workbench-local"
        for work in archived.get("works") or []:
            old = previous_works.get(str(work.get("aweme_id") or "")) or {}
            local_video = str(old.get("local_video") or "")
            if local_video and (self.root / local_video).is_file():
                work["local_video"] = local_video
                work["video_archived"] = True
            # The workbench archive is authoritative for human revisions. A stale
            # speech-to-text copy from the processing service must never replace
            # text edited while Docker was offline.
            if old.get("transcript_source") == "editor" and old.get("transcript_edited"):
                _repair_work_transcript(old)
                for key in (
                    "transcript",
                    "transcript_edited",
                    "transcript_source",
                    "transcript_revision",
                    "transcript_updated_at",
                    "transcript_versions",
                ):
                    if key in old:
                        work[key] = old[key]
        records[sec_uid] = archived
        self._save_account_cache(records)
        return archived

    def _remove_cached_account(self, sec_uid: str) -> bool:
        records = self._load_account_cache()
        removed = records.pop(sec_uid, None)
        if removed is None:
            return False
        self._save_account_cache(records)
        media_dir = self.account_media_root / sec_uid
        if media_dir.is_dir():
            shutil.rmtree(media_dir)
        return True

    def _local_video_path(self, sec_uid: str, aweme_id: str) -> Path | None:
        account = self._cached_account(sec_uid)
        if account:
            work = next(
                (
                    item
                    for item in account.get("works") or []
                    if str(item.get("aweme_id") or "") == aweme_id
                ),
                None,
            )
            if work:
                relative = str(work.get("local_video") or "")
                candidate = (self.root / relative).resolve() if relative else None
                if candidate and candidate.is_file() and self.root.resolve() in candidate.parents:
                    return candidate
        directory = self.account_media_root / sec_uid / aweme_id
        if directory.is_dir():
            return next(
                (
                    path
                    for path in directory.iterdir()
                    if path.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}
                ),
                None,
            )
        return None

    async def _remote_work_video_url(
        self, configured: DouyinRemoteSettings, work: dict[str, Any]
    ) -> tuple[str, str] | None:
        job_id = str(work.get("job_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
            return None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.connect_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"{configured.base_url}/api/v1/jobs/{job_id}/files",
                headers=self._headers(configured),
            )
            response.raise_for_status()
            files = response.json()
        video = next(
            (
                item
                for item in files
                if str(item.get("name") or "").lower().endswith((".mp4", ".mov", ".webm", ".m4v"))
            ),
            None,
        )
        if not video:
            return None
        token = str(video.get("url") or "").rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return None
        suffix = Path(str(video.get("name") or "video.mp4")).suffix.lower() or ".mp4"
        return f"{configured.base_url}/api/v1/jobs/{job_id}/files/{token}", suffix

    async def _archive_account_videos(self, account: dict[str, Any]) -> dict[str, Any]:
        configured = self._configured()
        sec_uid = str(account.get("sec_uid") or "")
        changed = False
        for work in account.get("works") or []:
            if work.get("processing_status") != "completed" or not work.get("job_id"):
                continue
            aweme_id = str(work.get("aweme_id") or "")
            existing = self._local_video_path(sec_uid, aweme_id)
            if existing:
                relative = existing.relative_to(self.root).as_posix()
                if work.get("local_video") != relative:
                    work["local_video"] = relative
                    work["video_archived"] = True
                    changed = True
                continue
            remote = await self._remote_work_video_url(configured, work)
            if remote is None:
                continue
            url, suffix = remote
            target_dir = self.account_media_root / sec_uid / aweme_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"video{suffix}"
            temporary = target.with_suffix(f"{suffix}.part")
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(configured.job_timeout_seconds),
                follow_redirects=False,
            ) as client:
                async with client.stream("GET", url, headers=self._headers(configured)) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                            output.write(chunk)
            temporary.replace(target)
            work["local_video"] = target.relative_to(self.root).as_posix()
            work["video_archived"] = True
            changed = True
        if changed:
            return self._cache_account(account)
        return account

    async def _store_account(self, account: dict[str, Any]) -> dict[str, Any]:
        archived = self._cache_account(account)
        try:
            return await self._archive_account_videos(archived)
        except (RuntimeError, httpx.HTTPError, OSError):
            return archived

    async def test_connection(self) -> dict[str, Any]:
        configured = self._configured()
        timeout = httpx.Timeout(configured.connect_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(
                f"{configured.base_url}/api/v1/health",
                headers=self._headers(configured),
            )
            response.raise_for_status()
            return response.json()

    async def sync_browser_cookies(self, cookies: list[dict[str, object]]) -> dict[str, Any]:
        configured = self._configured()
        local_host = urlparse(configured.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
        if not local_host:
            raise RuntimeError("为保护账号安全，登录凭证只能同步到本机抓取服务")
        pairs: list[str] = []
        seen: set[str] = set()
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "").strip()
            if name and value and name not in seen:
                pairs.append(f"{name}={value}")
                seen.add(name)
        if not pairs:
            raise RuntimeError("登录会话中没有找到可用的抖音 Cookie，请重新扫码登录")
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.connect_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.post(
                f"{configured.base_url}/api/settings/cookies",
                json={"cookie": "; ".join(pairs)},
            )
            response.raise_for_status()
            return response.json()

    async def _account_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        long_running: bool = False,
    ) -> Any:
        configured = self._configured()
        timeout_seconds = (
            configured.job_timeout_seconds if long_running else configured.connect_timeout_seconds
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.request(
                method,
                f"{configured.base_url}/api/v1/accounts{path}",
                headers=self._headers(configured),
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def resolve_account(self, request: DouyinAccountResolveRequest) -> dict[str, Any]:
        return await self._account_request("POST", "/resolve", payload=request.model_dump())

    async def list_accounts(self) -> list[dict[str, Any]]:
        try:
            remote = await self._account_request("GET", "")
        except (RuntimeError, httpx.HTTPError):
            return list(self._load_account_cache().values())
        stored = [await self._store_account(account) for account in remote]
        return stored

    async def save_account(self, sec_uid: str, account: dict[str, Any]) -> dict[str, Any]:
        value = await self._account_request("POST", f"/{sec_uid}", payload=account)
        return await self._store_account(value)

    async def get_account(self, sec_uid: str) -> dict[str, Any]:
        try:
            remote = await self._account_request("GET", f"/{sec_uid}")
        except (RuntimeError, httpx.HTTPError):
            cached = self._cached_account(sec_uid)
            if cached is None:
                raise KeyError("账号档案不存在") from None
            return cached
        return await self._store_account(remote)

    async def delete_account(self, sec_uid: str) -> dict[str, Any]:
        remote_deleted = False
        try:
            result = await self._account_request("DELETE", f"/{sec_uid}")
            remote_deleted = bool(result.get("deleted"))
        except (RuntimeError, httpx.HTTPError):
            pass
        local_deleted = self._remove_cached_account(sec_uid)
        return {"deleted": remote_deleted or local_deleted, "local_deleted": local_deleted}

    async def sync_account(self, sec_uid: str, request: DouyinAccountSyncRequest) -> dict[str, Any]:
        value = await self._account_request(
            "POST",
            f"/{sec_uid}/sync",
            payload=request.model_dump(),
            long_running=True,
        )
        return await self._store_account(value)

    async def batch_account(
        self, sec_uid: str, request: DouyinAccountBatchRequest
    ) -> dict[str, Any]:
        value = await self._account_request(
            "POST",
            f"/{sec_uid}/batch",
            payload=request.model_dump(),
            long_running=True,
        )
        try:
            account = await self._account_request("GET", f"/{sec_uid}")
            await self._store_account(account)
        except (RuntimeError, httpx.HTTPError):
            pass
        return value

    async def get_account_job(self, sec_uid: str, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
            raise KeyError("提取任务不存在")
        account = self._cached_account(sec_uid)
        if account is None:
            try:
                account = await self._account_request("GET", f"/{sec_uid}")
            except (RuntimeError, httpx.HTTPError):
                account = None
        belongs_to_account = any(
            str(work.get("job_id") or "") == job_id for work in (account or {}).get("works") or []
        )
        if not belongs_to_account:
            raise KeyError("提取任务不属于当前账号")
        configured = self._configured()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.connect_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"{configured.base_url}/api/v1/jobs/{job_id}",
                headers=self._headers(configured),
            )
            response.raise_for_status()
            return response.json()

    async def refresh_account_archive_when_jobs_finish(
        self, sec_uid: str, job_ids: list[str], *, poll_seconds: float = 5.0
    ) -> None:
        pending = {str(job_id) for job_id in job_ids if job_id}
        if not pending:
            return
        configured = self._configured()
        deadline = datetime.now(UTC).timestamp() + configured.job_timeout_seconds
        terminal = {"completed", "failed", "cancelled", "interrupted"}
        import asyncio

        while pending and datetime.now(UTC).timestamp() < deadline:
            try:
                account = await self._account_request("GET", f"/{sec_uid}")
                await self._store_account(account)
                statuses = {
                    str(work.get("job_id") or ""): str(work.get("processing_status") or "")
                    for work in account.get("works") or []
                }
                pending = {job_id for job_id in pending if statuses.get(job_id) not in terminal}
            except (RuntimeError, httpx.HTTPError, OSError):
                pass
            if pending:
                await asyncio.sleep(poll_seconds)

    async def analyze_account(
        self, sec_uid: str, request: DouyinAccountAnalyzeRequest
    ) -> dict[str, Any]:
        account = await self.get_account(sec_uid)
        transcribed = [
            work for work in account.get("works") or [] if str(work.get("transcript") or "").strip()
        ][: request.sample_size]
        if len(transcribed) < 5:
            raise RuntimeError("至少需要 5 条完整文案才能进行 AI 风格分析")
        analysis, model_info = await self.ai_models.analyze_creator(
            str(account.get("nickname") or "对标账号"), transcribed
        )
        value = analysis.model_dump()
        sample_count = len(account.get("works") or [])
        value.update(
            {
                "confidence": "高" if len(transcribed) >= 20 else "中",
                "sample_count": sample_count,
                "transcript_count": len(transcribed),
                "completion_ratio": round(len(transcribed) / max(sample_count, 1) * 100, 1),
                "analysis_engine": model_info,
            }
        )
        pillars = value["content_pillars"]
        if not all(isinstance(item.get("ratio"), (int, float)) for item in pillars):
            total_evidence = sum(max(int(item.get("evidence_count") or 1), 1) for item in pillars)
            for item in pillars:
                item["ratio"] = round(
                    max(int(item.get("evidence_count") or 1), 1) / total_evidence * 100, 1
                )
        portrait = {
            "generated_at": model_info["generated_at"],
            "sample_size": sample_count,
            "transcribed_count": len(transcribed),
            "positioning": value["positioning"],
            "content_pillars": pillars,
            "top_hashtags": [],
            "style_observations": value["author_lens"],
            "metrics": {},
            "representative_works": [
                {"aweme_id": work.get("aweme_id"), "title": work.get("title")}
                for work in transcribed[:8]
            ],
            "methodology": value,
        }
        account["portrait"] = portrait
        self._cache_account(account)
        return portrait

    async def update_account_work_transcript(
        self, sec_uid: str, aweme_id: str, text: str
    ) -> dict[str, Any]:
        try:
            updated = await self._account_request(
                "PUT",
                f"/{sec_uid}/works/{aweme_id}/transcript",
                payload={"text": text},
            )
        except (RuntimeError, httpx.HTTPError):
            account = self._cached_account(sec_uid)
            if account is None:
                raise KeyError("账号档案不存在") from None
            updated = None
            for work in account.get("works") or []:
                if str(work.get("aweme_id") or "") != aweme_id:
                    continue
                previous = str(work.get("transcript") or "").strip()
                if previous and previous != text.strip():
                    versions = list(work.get("transcript_versions") or [])
                    versions.append(
                        {
                            "text": previous,
                            "source": "editor",
                            "saved_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    work["transcript_versions"] = versions[-10:]
                work["transcript"] = text.strip()
                work["transcript_edited"] = text.strip()
                work["transcript_source"] = "editor"
                work["transcript_revision"] = int(work.get("transcript_revision") or 0) + 1
                work["transcript_updated_at"] = datetime.now(UTC).isoformat()
                updated = work
                break
            if updated is None:
                raise KeyError("作品不存在") from None
            self._cache_account(account)
            return updated
        account = await self._account_request("GET", f"/{sec_uid}")
        await self._store_account(account)
        return updated

    async def export_account_skill(self, sec_uid: str) -> tuple[bytes, str]:
        account = self._cached_account(sec_uid)
        if account is None:
            account = await self.get_account(sec_uid)
        portrait = account.get("portrait") or {}
        methodology = portrait.get("methodology") or {}
        if not methodology.get("writing_workflows"):
            raise RuntimeError("请先使用 DeepSeek 完成画像与写作方法论分析")
        nickname = str(account.get("nickname") or "对标账号")
        stable_id = re.sub(r"[^a-z0-9]", "", sec_uid.lower())[-10:] or "creator"
        skill_name = f"write-original-{stable_id}-style"
        skill_dir = self.root / "skills" / skill_name
        refs = skill_dir / "references"
        agents = skill_dir / "agents"
        refs.mkdir(parents=True, exist_ok=True)
        agents.mkdir(parents=True, exist_ok=True)

        skill_md = f"""---
name: {skill_name}
description: >-
  从“{nickname}”逐字稿提炼深层写作方法，创作原创中文短视频人生叙事、
  身份叙事和分级盘点文案。用于迁移沉浸视角、感官细节、人生压缩、
  现实磨损、情绪回环和克制收束等机制时；不得复制原句、比喻或案例顺序。
---

# 原创写作工作流

1. 必须读取 `references/writing-engine.md`，根据题材选择对应栏目母体。
2. 必须读取 `references/rewrite-checklist.md`，在交付前完成逐项重写检查。
3. 先建立事实材料表，再选择物件、感官、动作和人生节点。
   资料不足时明确采用虚构人物，不得捏造真实人物事实。
4. 完整稿先写结构骨架，再写画面，最后压低直接抒情；不得从金句开始反推故事。
5. 默认输出约 3 分钟的干净中文口播稿。用户指定时长时，按每分钟约 260–320 个汉字调整。
6. 只迁移写作机制，保持主题、事实、人物、场景、比喻和收束表达原创。
"""
        workflow_lines = []
        for flow in methodology.get("writing_workflows") or []:
            workflow_lines.append(f"## {flow.get('name')}\n\n适用：{flow.get('use_when')}\n")
            workflow_lines.extend(
                f"{index}. {step}" for index, step in enumerate(flow.get("steps") or [], 1)
            )
            required = "、".join(flow.get("required_elements") or [])
            workflow_lines.append(f"\n必备元素：{required}\n")
        engine_md = (
            "# 写作引擎\n\n## 作者视角\n\n"
            + "\n".join(f"- {item}" for item in methodology.get("author_lens") or [])
            + "\n\n## 细节选择\n\n"
            + "\n".join(f"- {item}" for item in methodology.get("detail_selection") or [])
            + "\n\n## 情绪推进\n\n"
            + "\n".join(f"- {item}" for item in methodology.get("emotional_arc") or [])
            + "\n\n## 语言执行\n\n"
            + "\n".join(f"- {item}" for item in methodology.get("language_mechanics") or [])
            + "\n\n"
            + "\n".join(workflow_lines)
        )
        checklist_md = (
            "# 重写检查\n\n"
            + "\n".join(f"- [ ] {item}" for item in methodology.get("rewrite_checks") or [])
            + "\n\n# 原创边界\n\n"
            + "\n".join(f"- {item}" for item in methodology.get("originality_rules") or [])
        )
        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (refs / "writing-engine.md").write_text(engine_md, encoding="utf-8")
        (refs / "rewrite-checklist.md").write_text(checklist_md, encoding="utf-8")
        (refs / "analysis.json").write_text(
            json.dumps(methodology, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (agents / "openai.yaml").write_text(
            f'''interface:
  display_name: "{nickname}式原创写作"
  short_description: "以深层叙事机制创作原创短视频文案"
  default_prompt: "请使用 ${skill_name} 创作一篇约三分钟的原创中文口播稿。"
''',
            encoding="utf-8",
        )
        archive = self.root / "skills" / f"{skill_name}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in skill_dir.rglob("*"):
                if path.is_file():
                    output.write(path, Path(skill_name) / path.relative_to(skill_dir))
        account["skill_export"] = {
            "name": skill_name,
            "generated_at": datetime.now(UTC).isoformat(),
            "confidence": methodology.get("confidence"),
            "transcript_count": methodology.get("transcript_count"),
            "model": (methodology.get("analysis_engine") or {}).get("model"),
        }
        self._cache_account(account)
        return archive.read_bytes(), archive.name

    async def _account_work_video_url(
        self, sec_uid: str, aweme_id: str
    ) -> tuple[DouyinRemoteSettings, str]:
        account = await self.get_account(sec_uid)
        work = next(
            (item for item in account.get("works") or [] if str(item.get("aweme_id")) == aweme_id),
            None,
        )
        if work is None:
            raise KeyError("作品不存在")
        job_id = str(work.get("job_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
            raise KeyError("这条作品还没有可播放的本地视频")
        configured = self._configured()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.connect_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"{configured.base_url}/api/v1/jobs/{job_id}/files",
                headers=self._headers(configured),
            )
            response.raise_for_status()
            files = response.json()
        video = next(
            (
                item
                for item in files
                if str(item.get("name") or "").lower().endswith((".mp4", ".mov", ".webm", ".m4v"))
            ),
            None,
        )
        if video is None:
            raise KeyError("这条作品的本地视频文件不存在")
        token = str(video.get("url") or "").rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise KeyError("视频文件地址无效")
        return configured, f"{configured.base_url}/api/v1/jobs/{job_id}/files/{token}"

    def account_work_local_video(self, sec_uid: str, aweme_id: str) -> Path | None:
        return self._local_video_path(sec_uid, aweme_id)

    async def account_work_video_headers(
        self, sec_uid: str, aweme_id: str, range_header: str | None = None
    ) -> dict[str, str]:
        local = self._local_video_path(sec_uid, aweme_id)
        if local:
            total = local.stat().st_size
            result = {
                "content-type": "video/mp4",
                "content-length": str(total),
                "accept-ranges": "bytes",
            }
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header or "")
            if match:
                start = int(match.group(1))
                end = min(int(match.group(2)) if match.group(2) else total - 1, total - 1)
                if start <= end:
                    result["content-range"] = f"bytes {start}-{end}/{total}"
                    result["content-length"] = str(end - start + 1)
            return result
        configured, url = await self._account_work_video_url(sec_uid, aweme_id)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.connect_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.head(url, headers=self._headers(configured))
            response.raise_for_status()
            result = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower()
                in {
                    "content-type",
                    "content-length",
                    "content-range",
                    "accept-ranges",
                }
            }
        result["accept-ranges"] = "bytes"
        total = int(result.get("content-length") or 0)
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header or "")
        if match and total:
            start = int(match.group(1))
            end = min(int(match.group(2)) if match.group(2) else total - 1, total - 1)
            if start <= end:
                result["content-range"] = f"bytes {start}-{end}/{total}"
                result["content-length"] = str(end - start + 1)
        elif range_header:
            result.pop("content-length", None)
        return result

    async def stream_account_work_video(
        self, sec_uid: str, aweme_id: str, range_header: str | None = None
    ) -> AsyncIterator[bytes]:
        local = self._local_video_path(sec_uid, aweme_id)
        if local:
            total = local.stat().st_size
            start, end = 0, total - 1
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header or "")
            if match:
                start = int(match.group(1))
                end = min(int(match.group(2)) if match.group(2) else total - 1, total - 1)
            remaining = max(0, end - start + 1)
            with local.open("rb") as source:
                source.seek(start)
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            return
        configured, url = await self._account_work_video_url(sec_uid, aweme_id)
        headers = self._headers(configured)
        if range_header:
            headers["Range"] = range_header
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.job_timeout_seconds),
            follow_redirects=False,
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    yield chunk

    async def create_job(self, request: DouyinExtractRequest) -> dict[str, Any]:
        configured = self._configured()
        timeout = httpx.Timeout(configured.connect_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                f"{configured.base_url}/api/v1/jobs",
                headers=self._headers(configured),
                json=request.model_dump(),
            )
            response.raise_for_status()
            remote = response.json()
        local_id = uuid4().hex
        jobs = self._load_jobs()
        jobs[local_id] = {
            "local_job_id": local_id,
            "remote_job_id": remote.get("job_id") or remote.get("id"),
            "source_url": remote.get("url"),
            "created_at": datetime.now(UTC).isoformat(),
            "imported_at": None,
            "import_dir": None,
        }
        self._save_jobs(jobs)
        return {**jobs[local_id], "remote": remote}

    def _mapping(self, local_job_id: str) -> dict[str, Any]:
        mapping = self._load_jobs().get(local_job_id)
        if mapping is None:
            raise KeyError("提取任务不存在")
        return mapping

    async def get_job(self, local_job_id: str) -> dict[str, Any]:
        configured = self._configured()
        mapping = self._mapping(local_job_id)
        timeout = httpx.Timeout(configured.connect_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(
                f"{configured.base_url}/api/v1/jobs/{mapping['remote_job_id']}",
                headers=self._headers(configured),
            )
            response.raise_for_status()
            remote = response.json()
        return {**mapping, "remote": remote}

    async def cancel_job(self, local_job_id: str) -> dict[str, Any]:
        configured = self._configured()
        mapping = self._mapping(local_job_id)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.connect_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.post(
                f"{configured.base_url}/api/v1/jobs/{mapping['remote_job_id']}/cancel",
                headers=self._headers(configured),
            )
            response.raise_for_status()
            return {**mapping, "remote": response.json()}

    def _remote_file_url(self, local_job_id: str, token: str) -> tuple[DouyinRemoteSettings, str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise KeyError("文件不存在")
        configured = self._configured()
        mapping = self._mapping(local_job_id)
        url = f"{configured.base_url}/api/v1/jobs/{mapping['remote_job_id']}/files/{token}"
        return configured, url

    async def file_headers(self, local_job_id: str, token: str) -> dict[str, str]:
        configured, url = self._remote_file_url(local_job_id, token)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.connect_timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.head(url, headers=self._headers(configured))
            response.raise_for_status()
            return {
                key: value
                for key, value in response.headers.items()
                if key.lower()
                in {"content-type", "content-length", "content-disposition", "accept-ranges"}
            }

    async def stream_file(
        self,
        local_job_id: str,
        token: str,
        range_header: str | None = None,
    ) -> AsyncIterator[bytes]:
        configured, url = self._remote_file_url(local_job_id, token)
        headers = self._headers(configured)
        if range_header:
            headers["Range"] = range_header
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.job_timeout_seconds),
            follow_redirects=False,
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    yield chunk

    async def import_job(self, local_job_id: str) -> dict[str, Any]:
        snapshot = await self.get_job(local_job_id)
        remote = snapshot["remote"]
        if remote.get("status") != "completed":
            raise RuntimeError("远程任务尚未完成")
        result = remote.get("result") or {}
        source_id = str(result.get("aweme_id") or snapshot["remote_job_id"])
        target = self.import_root / source_id
        target.mkdir(parents=True, exist_ok=True)
        configured = self._configured()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.job_timeout_seconds),
            follow_redirects=False,
        ) as client:
            for entry in remote.get("files") or []:
                name = Path(str(entry.get("name") or "file")).name
                if name.endswith(".log"):
                    continue
                remote_url = str(entry.get("url") or "")
                token = remote_url.rsplit("/", 1)[-1]
                if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
                    continue
                temporary = target / f".{name}.part"
                existing = temporary.stat().st_size if temporary.is_file() else 0
                headers = self._headers(configured)
                if existing:
                    headers["Range"] = f"bytes={existing}-"
                remote_url = (
                    f"{configured.base_url}/api/v1/jobs/{snapshot['remote_job_id']}/files/{token}"
                )
                async with client.stream("GET", remote_url, headers=headers) as response:
                    if existing and response.status_code == 200:
                        existing = 0
                        temporary.unlink(missing_ok=True)
                    response.raise_for_status()
                    mode = "ab" if existing else "wb"
                    with temporary.open(mode) as output:
                        async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                            output.write(chunk)
                expected = entry.get("size")
                if isinstance(expected, int) and temporary.stat().st_size != expected:
                    raise OSError(f"文件下载不完整：{name}")
                temporary.replace(target / name)
        metadata = {
            "source": "douyin",
            "source_url": result.get("source_url"),
            "source_id": source_id,
            "title": result.get("title"),
            "author": result.get("author"),
            "description": result.get("description"),
            "language": result.get("detected_language"),
            "duration": result.get("duration"),
            "transcript": result.get("transcript"),
            "segments": result.get("segments"),
            "rights_status": "user_confirmed",
            "imported_at": datetime.now(UTC).isoformat(),
        }
        (target / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        jobs = self._load_jobs()
        jobs[local_job_id]["imported_at"] = metadata["imported_at"]
        jobs[local_job_id]["import_dir"] = str(target)
        self._save_jobs(jobs)
        return {
            **jobs[local_job_id],
            "metadata": metadata,
            "files": [path.name for path in target.iterdir()],
        }

    def list_imports(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for metadata_path in self.import_root.glob("*/metadata.json"):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            directory = metadata_path.parent
            video = next(directory.glob("*.mp4"), None)
            items.append(
                {
                    **payload,
                    "directory": str(directory),
                    "video_name": video.name if video else None,
                }
            )
        return sorted(items, key=lambda item: str(item.get("imported_at") or ""), reverse=True)

    def imported_video(self, source_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", source_id):
            raise KeyError("导入素材不存在")
        directory = (self.import_root / source_id).resolve()
        if directory.parent != self.import_root.resolve() or not directory.is_dir():
            raise KeyError("导入素材不存在")
        video = next(directory.glob("*.mp4"), None)
        if video is None or not video.is_file():
            raise KeyError("导入视频不存在")
        return video
