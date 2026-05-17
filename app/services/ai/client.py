"""Gemini API 通信のみ（プロンプト・ドメイン解析は各 parser に委譲）"""

import json
import os
import re

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-2.5-flash"
# gemini-1.5-flash は v1beta で廃止。利用可能なモデルへフォールバックする。
FALLBACK_MODELS = (
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
)


def get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY が設定されていません。.env に GEMINI_API_KEY=... を追加してください。"
        )
    return key


def get_model_name() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _models_to_try() -> list[str]:
    """主モデル + 未試行のフォールバックモデル（重複なし）"""
    chain: list[str] = []
    for name in (get_model_name(), *FALLBACK_MODELS):
        if name and name not in chain:
            chain.append(name)
    return chain


def _should_try_next_model(exc: Exception) -> bool:
    """別モデルで再試行する価値があるエラーか"""
    msg = str(exc).lower()
    if "404" in msg or "not found" in msg or "not supported" in msg:
        return True
    if "503" in msg or "unavailable" in msg or "overloaded" in msg:
        return True
    if "429" in msg or "quota" in msg or "rate" in msg:
        return True
    return False


def extract_json_from_text(text: str) -> dict:
    """Gemini 返答から JSON オブジェクトを抽出する（フェンス・説明文混在に対応）"""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("空のレスポンスです")

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    brace_match = re.search(r"\{[\s\S]*\}", cleaned)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON のパースに失敗しました: {exc}") from exc

    raise ValueError("レスポンスから JSON オブジェクトを抽出できませんでした")


def generate_content(
    prompt: str,
    *,
    response_schema: type | None = None,
    temperature: float = 0.2,
) -> str:
    """Gemini にプロンプトを送り、生テキスト応答を返す"""
    client = genai.Client(api_key=get_api_key())

    config_kwargs: dict = {
        "response_mime_type": "application/json",
        "temperature": temperature,
    }
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema

    config = types.GenerateContentConfig(**config_kwargs)

    models_chain = _models_to_try()
    errors: list[str] = []

    for name in models_chain:
        try:
            response = client.models.generate_content(
                model=name,
                contents=prompt,
                config=config,
            )
            if response.text:
                return response.text
            raise ValueError("Gemini から空のテキストが返されました")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            if name != models_chain[-1] and _should_try_next_model(exc):
                continue
            break

    detail = " → ".join(errors) if errors else "不明なエラー"
    raise RuntimeError(
        f"Gemini API 呼び出しに失敗しました（試行: {', '.join(models_chain)}）: {detail}"
    )
