"""自然文 → 食事・作り置き・ライフサイクルイベントの構造化（プロンプト管理）"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.models import FOOD_EVENT_TYPES
from app.services.ai.client import extract_json_from_text, generate_content

ALLOWED_EVENT_TYPES = FOOD_EVENT_TYPES

_FIELDS_BY_EVENT_TYPE: dict[str, frozenset[str]] = {
    "meal": frozenset({"meal_type", "items"}),
    "batch_created": frozenset({"batch_name", "servings"}),
    "consumed": frozenset({
        "target",
        "source",
        "quantity_total",
        "quantity_used",
        "quantity_remaining",
    }),
    "purchase": frozenset({"items"}),
    "unknown": frozenset(),
}


def fields_for_event_type(event_type: str) -> frozenset[str]:
    return _FIELDS_BY_EVENT_TYPE.get(event_type, frozenset())


def strip_to_event_type(data: dict, event_type: str) -> dict:
    """event_type に属さないキーを除去（修正時の型崩れ防止）"""
    allowed = fields_for_event_type(event_type) | {"event_type", "confidence"}
    return {k: v for k, v in data.items() if k in allowed and v is not None}

_PROMPT_TEMPLATE = """以下の日本語テキストを食事・食品に関する生活イベントとして解析してください。

返答は JSON のみ。説明文やマークダウンは付けないでください。
テキストから読み取れる事実だけを入れ、推測しすぎないでください。
分類できない場合は event_type を unknown にしてください。

event_type（いずれか1つ）:
- meal: 食事の摂取（朝・昼・夜・間食 + 食品名）
- batch_created: 作り置き・調理（「作った」「作り置き」など）
- consumed: 使い切った・飲み切った・なくなった
- purchase: 買い物（レシート風・購入の明示がある場合のみ）
- unknown: 分類不能

meal のとき:
- meal_type: breakfast | lunch | dinner | snack
- items: [{{"name": "食品名", "quantity": 数値}}]  quantity は不明なら 1

batch_created のとき:
- batch_name: 料理・食品名
- servings: 食数（整数。不明なら省略）

consumed / 一部使用 のとき:
- target: 食品名のみ（短く。店名・数量・状態は入れない）
- source: 購入店・入手元（任意）
- quantity_total: 合計数量・人前（任意）
- quantity_used: 今回使った数量（任意）
- quantity_remaining: 残り数量（任意）
- 複数の事実を target 1つに連結しないこと

purchase のとき（今回は最小限）:
- items: [{{"name": "商品名", "quantity": 数値}}]

出力例（昼食）:
{{
  "event_type": "meal",
  "meal_type": "lunch",
  "items": [{{"name": "カレー", "quantity": 1}}],
  "confidence": 0.9
}}

出力例（作り置き）:
{{
  "event_type": "batch_created",
  "batch_name": "カレー",
  "servings": 6,
  "confidence": 0.9
}}

出力例（使い切り）:
{{
  "event_type": "consumed",
  "target": "牛乳",
  "confidence": 0.9
}}

出力例（一部使用）:
{{
  "event_type": "consumed",
  "target": "ナポリタンの素",
  "source": "業務スーパー",
  "quantity_total": 3,
  "quantity_used": 1,
  "quantity_remaining": 2,
  "confidence": 0.9
}}

Text:
{text}
"""


class FoodItem(BaseModel):
    name: str
    quantity: float | int | None = Field(default=1)


class FoodEventParseResult(BaseModel):
    event_type: Literal["meal", "batch_created", "consumed", "purchase", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    items: list[FoodItem] | None = None
    batch_name: str | None = None
    servings: int | None = None
    target: str | None = None
    source: str | None = None
    quantity_total: float | int | None = None
    quantity_used: float | int | None = None
    quantity_remaining: float | int | None = None


_MEAL_TYPE_ALIASES = {
    "朝": "breakfast",
    "朝食": "breakfast",
    "breakfast": "breakfast",
    "昼": "lunch",
    "昼食": "lunch",
    "ランチ": "lunch",
    "lunch": "lunch",
    "夜": "dinner",
    "夕": "dinner",
    "夕食": "dinner",
    "晩": "dinner",
    "晩ごはん": "dinner",
    "dinner": "dinner",
    "間食": "snack",
    "おやつ": "snack",
    "snack": "snack",
}


def _normalize_quantity(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_consumed_fields(data: dict) -> dict:
    """consumed 用フィールドを正規化（target への連結を防ぐ）"""
    parsed: dict = {}

    target = data.get("target")
    if isinstance(target, str) and target.strip():
        cleaned = _clean_consumed_target(target.strip())
        if cleaned:
            parsed["target"] = cleaned

    source = data.get("source")
    if isinstance(source, str) and source.strip():
        parsed["source"] = source.strip()

    for key in ("quantity_total", "quantity_used", "quantity_remaining"):
        qty = _normalize_quantity(data.get(key))
        if qty is not None and qty >= 0:
            parsed[key] = qty

    return parsed


def _clean_consumed_target(target: str) -> str:
    """AI が連結しがちな target を短い商品名へ寄せる"""
    lower = target.lower()
    if "consumed" in lower or "remaining" in lower or " - " in target:
        # 「名前（3人前）- 1人前consumed...」型の連結を分割
        head = target.split(" - ")[0].split("-")[0].strip()
        # 括弧内の数量表記を除去
        head = re.sub(r"[（(]\s*\d+\s*人前\s*[）)]", "", head).strip()
        head = re.sub(r"で買った", "", head).strip()
        for prefix in ("業務スーパー", "スーパー"):
            if head.startswith(prefix):
                head = head[len(prefix) :].strip()
        return head or target.split("（")[0].split("(")[0].strip()
    return target


def _normalize_items(items: object) -> list[dict]:
    if not isinstance(items, list):
        return []
    result: list[dict] = []
    for item in items:
        if hasattr(item, "model_dump"):
            item = item.model_dump(exclude_none=True)
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        qty = item.get("quantity", 1)
        try:
            qty = float(qty) if qty is not None else 1.0
        except (TypeError, ValueError):
            qty = 1.0
        result.append({"name": name, "quantity": qty})
    return result


def _build_parsed_json(data: dict, event_type: str) -> dict:
    """event_type 列用の parsed_json（event_type 本体は列に保存）"""
    parsed: dict = {}

    if event_type == "meal":
        meal_type = data.get("meal_type")
        if isinstance(meal_type, str):
            meal_type = _MEAL_TYPE_ALIASES.get(meal_type.strip(), meal_type.strip().lower())
            if meal_type in ("breakfast", "lunch", "dinner", "snack"):
                parsed["meal_type"] = meal_type
        items = _normalize_items(data.get("items"))
        if items:
            parsed["items"] = items

    elif event_type == "batch_created":
        batch_name = data.get("batch_name")
        if isinstance(batch_name, str) and batch_name.strip():
            parsed["batch_name"] = batch_name.strip()
        servings = data.get("servings")
        if servings is not None:
            try:
                parsed["servings"] = int(servings)
            except (TypeError, ValueError):
                pass

    elif event_type == "consumed":
        parsed.update(_normalize_consumed_fields(data))

    elif event_type == "purchase":
        items = _normalize_items(data.get("items"))
        if items:
            parsed["items"] = items

    return parsed


def _is_effectively_empty(parsed_json: dict) -> bool:
    return not parsed_json


def validate_food_parse_result(data: dict, *, raw_text: str = "") -> dict:
    if not isinstance(data, dict):
        raise ValueError("解析結果がオブジェクトではありません")

    event_type = data.get("event_type", "unknown")
    if not isinstance(event_type, str) or event_type not in ALLOWED_EVENT_TYPES:
        event_type = "unknown"

    parsed_json = _build_parsed_json(data, event_type)

    if _is_effectively_empty(parsed_json) and raw_text.strip() and event_type != "unknown":
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


def parse_food_event_text(raw_text: str) -> dict:
    """
    自然文を食事イベントとして解析する。

    Returns:
        {"event_type": str, "parsed_json": dict, "confidence": float}
    """
    text = raw_text.strip()
    if not text:
        raise ValueError("解析対象のテキストが空です")

    prompt = _PROMPT_TEMPLATE.format(text=text)
    response_text = generate_content(prompt, response_schema=FoodEventParseResult)

    try:
        raw_data = extract_json_from_text(response_text)
    except ValueError as exc:
        raise ValueError(f"AI 応答の JSON 抽出に失敗しました: {exc}") from exc

    try:
        model = FoodEventParseResult.model_validate(raw_data)
        raw_data = model.model_dump(exclude_none=True)
    except Exception:
        pass

    return validate_food_parse_result(raw_data, raw_text=text)
