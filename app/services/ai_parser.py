"""Gemini API によるキャプチャ文字列の構造化解析"""

import json
import os
import re
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.models import EVENT_TYPES

ALLOWED_EVENT_TYPES = EVENT_TYPES
DEFAULT_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-1.5-flash"

# parsed_json に保存してよいキー（この一覧以外は捨てる）
_PARSED_JSON_KEYS = frozenset({
    "store",
    "date",
    "currency",
    "items",
    "subtotal",
    "tax",
    "total",
    "payment",
})

# フラット入力を payment 配列へ寄せる内部キー
_INTERNAL_PAYMENT_KEYS = frozenset({"payment_method", "card_last4"})

# Gemini 出力の別名 → 正規キー
_FIELD_ALIASES = {
    "shop": "store",
    "products": "items",
    "payments": "payment",
    "決済": "payment",
    "決済方法": "payment_method",
    "支払方法": "payment_method",
    "支払い方法": "payment_method",
    "card_last_four": "card_last4",
    "card_last_4": "card_last4",
    "last4": "card_last4",
    "カード下4桁": "card_last4",
    "カード番号下4桁": "card_last4",
    "カード下４桁": "card_last4",
}

_PROMPT_TEMPLATE = """以下のテキストを生活イベントとして解析してください。

返答は JSON のみ。説明文やマークダウンは付けないでください。
テキストから読み取れる事実だけを入れ、推測しすぎないでください。
不明な項目は省略して構いませんが、event_type が unknown 以外のときは
store / items / total のいずれかを必ず埋めてください。

event_type（いずれか1つ）:
- purchase: 買い物・レシート・支払い
- inventory: 在庫・残量
- food: 食事・料理
- unknown: 分類不能

parsed_json に入れてよい項目（このキー名のみ。不明なものは省略）:
- store: 店名・施設名
- date: 日付（YYYY-MM-DD など）
- currency: 通貨コード（例: JPY）
- items: 商品リスト [{{"name": "商品名", "price": 数値}}]
- subtotal: 小計
- tax: 税額
- total: 合計
- payment: 決済情報の配列。要素は1キーのオブジェクトのみ
  - {{"payment_method": "現金|クレジット|PayPay など"}}
  - {{"card_last4": "1234"}}  ※下4桁のみ。カード番号の全文は入れない

出力例:
{{
  "event_type": "purchase",
  "confidence": 0.9,
  "store": "OKストア",
  "date": "2025-05-18",
  "items": [{{"name": "牛乳", "price": 298}}],
  "subtotal": 298,
  "tax": 29,
  "total": 327,
  "currency": "JPY",
  "payment": [
    {{"payment_method": "クレジット"}},
    {{"card_last4": "1234"}}
  ]
}}

Text:
{text}
"""

_STRICT_RETRY_SUFFIX = """

重要: 前回の抽出が不十分でした。store と items（商品名・単価）を必ず入れてください。
レシートなら読み取れる商品を items に列挙し、合計があれば total も入れてください。
決済方法・カード下4桁が分かれば payment 配列に分けて入れてください。
"""

class PurchaseItem(BaseModel):
    name: str
    price: float | None = None


class PaymentEntry(BaseModel):
    """payment 配列の1要素（決済方法またはカード下4桁のどちらか一方）"""

    payment_method: str | None = Field(default=None, description="決済方法")
    card_last4: str | None = Field(default=None, description="カード下4桁")


class CaptureParseResult(BaseModel):
    """Gemini への構造化出力スキーマ（詳細はフラットに受け取り parsed_json へまとめる）"""

    event_type: Literal["purchase", "inventory", "food", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0)
    store: str | None = Field(default=None, description="店名・施設名")
    date: str | None = Field(default=None, description="日付 YYYY-MM-DD など")
    items: list[PurchaseItem] | None = Field(default=None, description="商品・品目リスト")
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    currency: str | None = Field(default="JPY")
    payment: list[PaymentEntry] | None = Field(
        default=None,
        description="決済情報（payment_method と card_last4 を分けた配列）",
    )
    payment_method: str | None = Field(
        default=None,
        description="（互換）決済方法。保存時は payment 配列へ変換",
    )
    card_last4: str | None = Field(
        default=None,
        description="（互換）カード下4桁。保存時は payment 配列へ変換",
    )


def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY が設定されていません。.env に GEMINI_API_KEY=... を追加してください。"
        )
    return key


def _get_model_name() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


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


def _normalize_card_last4(value: object) -> str | None:
    """カード下4桁を4桁の数字文字列に正規化（それ以外は保存しない）"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        text = str(int(value))
    else:
        text = str(value).strip()
    if not text:
        return None

    digits = re.sub(r"\D", "", text)
    if len(digits) < 4:
        return None
    return digits[-4:]


def _expand_payment_entry(entry: object) -> list[dict]:
    """payment 配列の1要素を、1キーずつのオブジェクトに展開"""
    if not isinstance(entry, dict):
        return []

    expanded: list[dict] = []
    method = str(entry.get("payment_method", "")).strip()
    if method:
        method_only, embedded_last4 = _split_payment_method_string(method)
        if method_only:
            expanded.append({"payment_method": method_only})
        if embedded_last4:
            expanded.append({"card_last4": embedded_last4})

    last4 = _normalize_card_last4(entry.get("card_last4"))
    if last4:
        expanded.append({"card_last4": last4})

    return expanded


def _split_payment_method_string(method: str) -> tuple[str, str | None]:
    """文字列末尾の ****1234 を分離（レガシー出力の救済）"""
    match = re.search(r"\*{2,}\s*(\d{4})\s*$", method)
    if not match:
        return method, None
    last4 = match.group(1)
    method_only = method[: match.start()].strip()
    return method_only, last4


def _build_payment_array(merged: dict) -> list[dict]:
    """フラットな決済フィールドと payment 配列を統合"""
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(entry: dict | None) -> None:
        if not entry:
            return
        key, value = next(iter(entry.items()))
        signature = (key, value)
        if signature in seen:
            return
        seen.add(signature)
        entries.append(entry)

    raw_payment = merged.get("payment")
    if isinstance(raw_payment, list):
        for item in raw_payment:
            if hasattr(item, "model_dump"):
                item = item.model_dump(exclude_none=True)
            for entry in _expand_payment_entry(item):
                add(entry)

    method_value = merged.get("payment_method")
    if method_value is not None:
        method_str = str(method_value).strip()
        if method_str:
            method_only, embedded_last4 = _split_payment_method_string(method_str)
            if method_only:
                add({"payment_method": method_only})
            if embedded_last4:
                add({"card_last4": embedded_last4})

    last4 = _normalize_card_last4(merged.get("card_last4"))
    if last4:
        add({"card_last4": last4})

    return entries


def _canonical_field_key(key: str) -> str | None:
    """別名を正規キーに変換。許可外なら None"""
    canonical = _FIELD_ALIASES.get(key, key)
    if canonical in _PARSED_JSON_KEYS or canonical in _INTERNAL_PAYMENT_KEYS:
        return canonical
    return None


def _normalize_parsed_json(data: dict) -> dict:
    """フラットな Gemini 出力を許可キーのみの parsed_json 辞書にまとめる"""
    merged: dict = {}
    nested = data.get("parsed_json")
    if isinstance(nested, dict):
        merged.update(nested)

    for key, value in data.items():
        if key in ("event_type", "confidence", "parsed_json"):
            continue
        if value is None:
            continue
        canonical = _canonical_field_key(key)
        if canonical is None:
            continue
        # 正規キーが既にあるときは別名で上書きしない（shop → store など）
        if canonical in merged and key != canonical:
            continue
        merged[canonical] = value

    payment_entries = _build_payment_array(merged)
    merged.pop("payment_method", None)
    merged.pop("card_last4", None)
    merged.pop("payment", None)
    if payment_entries:
        merged["payment"] = payment_entries

    parsed_json: dict = {}
    for key in _PARSED_JSON_KEYS:
        if key not in merged:
            continue
        value = merged[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        parsed_json[key] = value

    # Pydantic model_dump 後の items / payment を plain dict に
    items = parsed_json.get("items")
    if isinstance(items, list):
        parsed_json["items"] = [
            item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
            for item in items
        ]

    payment = parsed_json.get("payment")
    if isinstance(payment, list):
        normalized_payment: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in payment:
            if hasattr(item, "model_dump"):
                item = item.model_dump(exclude_none=True)
            for entry in _expand_payment_entry(item):
                signature = (next(iter(entry)), entry[next(iter(entry))])
                if signature in seen:
                    continue
                seen.add(signature)
                normalized_payment.append(entry)
        if normalized_payment:
            parsed_json["payment"] = normalized_payment
        else:
            del parsed_json["payment"]

    return parsed_json


def _is_effectively_empty(parsed_json: dict) -> bool:
    return not parsed_json


def validate_parse_result(data: dict, *, raw_text: str = "") -> dict:
    """解析結果の最低限のバリデーションと正規化"""
    if not isinstance(data, dict):
        raise ValueError("解析結果がオブジェクトではありません")

    event_type = data.get("event_type", "unknown")
    if not isinstance(event_type, str) or event_type not in ALLOWED_EVENT_TYPES:
        event_type = "unknown"

    parsed_json = _normalize_parsed_json(data)

    if _is_effectively_empty(parsed_json) and raw_text.strip():
        event_type = "unknown"

    confidence = data.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    if _is_effectively_empty(parsed_json):
        confidence = min(confidence, 0.3)

    return {
        "event_type": event_type,
        "parsed_json": parsed_json,
        "confidence": confidence,
    }


def _needs_retry(result: dict, raw_text: str) -> bool:
    if result["event_type"] == "unknown":
        return False
    if not _is_effectively_empty(result["parsed_json"]):
        return False
    return len(raw_text.strip()) >= 20


def _call_gemini(raw_text: str, *, strict: bool = False) -> str:
    client = genai.Client(api_key=_get_api_key())
    prompt = _PROMPT_TEMPLATE.format(text=raw_text)
    if strict:
        prompt += _STRICT_RETRY_SUFFIX

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=CaptureParseResult,
        temperature=0.2,
    )

    model_name = _get_model_name()
    models_to_try = [model_name]
    if model_name != FALLBACK_MODEL:
        models_to_try.append(FALLBACK_MODEL)

    last_error: Exception | None = None
    for name in models_to_try:
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
            last_error = exc

    raise RuntimeError(f"Gemini API 呼び出しに失敗しました: {last_error}") from last_error


def parse_capture_text(raw_text: str) -> dict:
    """
    raw_text を Gemini で解析し、正規化済みの dict を返す。

    Returns:
        {"event_type": str, "parsed_json": dict, "confidence": float}
    """
    text = raw_text.strip()
    if not text:
        raise ValueError("解析対象のテキストが空です")

    response_text = _call_gemini(text)
    raw_data = extract_json_from_text(response_text)
    # Pydantic スキーマ出力をフラット dict に展開
    if "parsed_json" not in raw_data or not raw_data.get("parsed_json"):
        try:
            model = CaptureParseResult.model_validate(raw_data)
            raw_data = model.model_dump(exclude_none=True)
        except Exception:
            pass
    result = validate_parse_result(raw_data, raw_text=text)

    if _needs_retry(result, text):
        retry_text = _call_gemini(text, strict=True)
        retry_result = validate_parse_result(
            extract_json_from_text(retry_text), raw_text=text
        )
        if not _is_effectively_empty(retry_result["parsed_json"]):
            return retry_result
        if len(retry_result["parsed_json"]) > len(result["parsed_json"]):
            return retry_result

    return result
