from __future__ import annotations

import base64
import ctypes
import json
import os
import re
from ctypes import wintypes
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from stock_video_generator.config import Settings

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")


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
            ctypes.byref(in_blob), "社媒工作台模型密钥", None, None, None, 0, ctypes.byref(out_blob)
        )
    if not ok:
        raise OSError("无法使用当前 Windows 用户保护模型密钥")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _encode_secret(value: str) -> str:
    return base64.b64encode(_dpapi(value.encode("utf-8"), decrypt=False)).decode("ascii")


def _decode_secret(value: str) -> str:
    return _dpapi(base64.b64decode(value), decrypt=True).decode("utf-8")


class AiModelSettings(BaseModel):
    enabled: bool = False
    provider: str = "deepseek"
    model: str = DEEPSEEK_MODELS[0]
    api_key: str | None = Field(default=None, repr=False)
    request_timeout_seconds: int = Field(default=300, ge=30, le=900)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value.strip().lower() != "deepseek":
            raise ValueError("当前版本仅支持 DeepSeek")
        return "deepseek"

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if value not in DEEPSEEK_MODELS:
            raise ValueError(f"DeepSeek 模型只能选择：{', '.join(DEEPSEEK_MODELS)}")
        return value


class AiModelSettingsUpdate(BaseModel):
    enabled: bool = True
    provider: str = "deepseek"
    model: str = DEEPSEEK_MODELS[0]
    api_key: str | None = None
    request_timeout_seconds: int = Field(default=300, ge=30, le=900)


class CreatorStyleAnalysis(BaseModel):
    positioning: str = Field(min_length=20)
    style_summary: str = Field(min_length=20)
    content_pillars: list[dict[str, Any]] = Field(min_length=1, max_length=8)
    author_lens: list[str] = Field(min_length=3, max_length=10)
    hook_patterns: list[dict[str, Any]] = Field(min_length=2, max_length=10)
    narrative_structures: list[dict[str, Any]] = Field(min_length=2, max_length=10)
    detail_selection: list[str] = Field(min_length=3, max_length=12)
    emotional_arc: list[str] = Field(min_length=3, max_length=12)
    language_mechanics: list[str] = Field(min_length=3, max_length=12)
    writing_workflows: list[dict[str, Any]] = Field(min_length=1, max_length=6)
    rewrite_checks: list[str] = Field(min_length=4, max_length=16)
    originality_rules: list[str] = Field(min_length=3, max_length=12)
    applicable_topics: list[str] = Field(default_factory=list, max_length=12)
    unsuitable_topics: list[str] = Field(default_factory=list, max_length=12)


class AiModelService:
    def __init__(self, settings: Settings) -> None:
        self.config_path = settings.data_dir / "settings" / "ai-model.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> AiModelSettings:
        if not self.config_path.is_file():
            return AiModelSettings()
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        encrypted = payload.pop("api_key_encrypted", None)
        payload["api_key"] = _decode_secret(encrypted) if encrypted else None
        return AiModelSettings.model_validate(payload)

    def public_settings(self) -> dict[str, Any]:
        configured = self.load_settings()
        payload = configured.model_dump(exclude={"api_key"})
        payload["api_key_configured"] = bool(configured.api_key)
        payload["api_key_hint"] = (
            f"••••••••{configured.api_key[-4:]}" if configured.api_key else None
        )
        payload["available_models"] = list(DEEPSEEK_MODELS)
        return payload

    def save_settings(self, request: AiModelSettingsUpdate) -> dict[str, Any]:
        current = self.load_settings()
        api_key = request.api_key.strip() if request.api_key else current.api_key
        configured = AiModelSettings(**request.model_dump(exclude={"api_key"}), api_key=api_key)
        payload = configured.model_dump(exclude={"api_key"})
        payload["api_key_encrypted"] = _encode_secret(api_key) if api_key else None
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)
        return self.public_settings()

    def _configured(self) -> AiModelSettings:
        configured = self.load_settings()
        if not configured.enabled:
            raise RuntimeError("AI 文案分析尚未启用，请先到系统设置配置 DeepSeek")
        if not configured.api_key:
            raise RuntimeError("尚未配置 DeepSeek API Key，请先到系统设置保存密钥")
        return configured

    async def test_connection(self) -> dict[str, Any]:
        configured = self._configured()
        async with httpx.AsyncClient(timeout=httpx.Timeout(30), follow_redirects=False) as client:
            response = await client.get(
                f"{DEEPSEEK_BASE_URL}/models",
                headers={"Authorization": f"Bearer {configured.api_key}"},
            )
            response.raise_for_status()
            model_ids = [str(item.get("id")) for item in response.json().get("data", [])]
        return {
            "status": "connected",
            "provider": "DeepSeek",
            "model": configured.model,
            "model_available": configured.model in model_ids,
            "available_models": model_ids,
        }

    @staticmethod
    def _analysis_prompt(nickname: str, works: list[dict[str, Any]]) -> str:
        samples = []
        for index, work in enumerate(works, 1):
            transcript = re.sub(r"\s+", " ", str(work.get("transcript") or "")).strip()
            samples.append(
                f"\n### 样本 {index}\n标题：{work.get('title') or '未命名'}\n"
                f"时长：{work.get('duration') or 0} 秒\n逐字稿：{transcript}"
            )
        return f"""你是一名中文叙事写作教练和短视频栏目建模专家。
请分析账号“{nickname}”的完整逐字稿。目标不是生成几个统计标签，
而是构建一套另一位模型能够真正执行的原创写作方法。

必须完成这些判断：
1. 作者如何观察普通人、身份、时间、家庭、欲望、尊严和苦难；
2. 如何从素材中选择能承载命运的感官细节、物件、动作与场景；
3. 如何压缩人物一生、安排年龄节点和现实磨损；
4. 如何建立开场、悬念、中段转折、情绪峰值与首尾回环；
5. 不同栏目母体必须分开建模，例如人生叙事与等级盘点不能混成一个模板；
6. 如何诊断并重写空泛、虚假、过度煽情、只有金句或缺少画面的稿件；
7. 明确哪些只是原作者独有表层表达，不能复制。

只返回一个合法 JSON 对象，不要 Markdown，不要解释。字段必须是：
positioning 字符串；style_summary 字符串；
content_pillars 数组，每项含 name、description、evidence_count、ratio（各项比例合计约 100）；
author_lens 字符串数组；
hook_patterns 数组，每项含 name、purpose、procedure、evidence_count；
narrative_structures 数组，每项含 name、use_when、steps（字符串数组）、evidence_count；
detail_selection 字符串数组；emotional_arc 字符串数组；language_mechanics 字符串数组；
writing_workflows 数组，每项含 name、use_when、steps（字符串数组）、
required_elements（字符串数组）；
rewrite_checks 字符串数组；originality_rules 字符串数组；
applicable_topics 字符串数组；unsuitable_topics 字符串数组。

要求：结论必须来自多篇样本；写成可执行命令，不写“多用细节、注意节奏”这类空话；不得大段引用原文；不得虚构作者经历或心理；不要把识别错字当成写作风格。

以下是逐字稿：{"".join(samples)}"""

    async def analyze_creator(
        self, nickname: str, works: list[dict[str, Any]]
    ) -> tuple[CreatorStyleAnalysis, dict[str, str]]:
        configured = self._configured()
        payload = {
            "model": configured.model,
            "messages": [
                {"role": "system", "content": "你只输出符合要求的 JSON 对象。"},
                {"role": "user", "content": self._analysis_prompt(nickname, works)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 12_000,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(configured.request_timeout_seconds), follow_redirects=False
        ) as client:
            response = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {configured.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
        try:
            analysis = CreatorStyleAnalysis.model_validate(json.loads(content))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"DeepSeek 返回的方法论结构不完整，请重新分析：{exc}") from exc
        return analysis, {
            "provider": "DeepSeek",
            "model": configured.model,
            "generated_at": datetime.now(UTC).isoformat(),
        }
