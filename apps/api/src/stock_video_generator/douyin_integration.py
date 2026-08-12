from __future__ import annotations

import base64
import ctypes
import json
import os
import re
from collections.abc import AsyncIterator
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, field_validator

from stock_video_generator.config import Settings


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
        ok = function(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
    else:
        ok = function(
            ctypes.byref(in_blob), "StockVideoGenerator", None, None, None, 0,
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
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.data_dir / "integrations" / "douyin"
        self.import_root = settings.data_dir / "imports" / "douyin"
        self.config_path = self.root / "settings.json"
        self.jobs_path = self.root / "jobs.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.import_root.mkdir(parents=True, exist_ok=True)

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
            "User-Agent": "StockVideoGenerator-DouyinIntegration/0.1.9",
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
        url = (
            f"{configured.base_url}/api/v1/jobs/{mapping['remote_job_id']}/files/{token}"
        )
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
                    f"{configured.base_url}/api/v1/jobs/"
                    f"{snapshot['remote_job_id']}/files/{token}"
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
