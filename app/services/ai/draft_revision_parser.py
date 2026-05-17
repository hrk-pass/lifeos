"""下書き JSON + 自然文修正指示 → 更新版 draft（プロンプト管理）"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from app.services.ai.client import extract_json_from_text, generate_content
from app.services.ai.food_event_parser import (
    FoodItem,
    fields_for_event_type,
    strip_to_event_type,
    validate_food_parse_result,
)

_TYPE_CHANGE_MARKERS = (
    "使い切",
    "飲み切",
    "なくな",
    "切らした",
    "作り置き",
    "作った",
    "炊いた",
    "調理した",
    "買った",
    "購入",
    "レシート",
    "event_type",
    "種別",
    "タイプ",
)

_REVISION_BASE = """以下の食事イベント下書きを、人間の修正指示どおりに更新してください。

元の自然文（不変）:
{raw_text}

現在の event_type: {event_type}
現在の下書き JSON:
{draft_json}

修正指示:
{instruction}

ルール:
- 返答は JSON のみ
- **修正指示を必ず反映**した完全な下書きを返す（変更がない場合はない）
- event_type は {event_type} のまま（変更しない）
- 使えるフィールドだけ返す

{type_rules}

必ず confidence（0.0〜1.0）を含めてください。
"""

_STRICT_SUFFIX = """

重要: 前回の出力は修正指示を反映していませんでした。
修正指示「{instruction}」を必ず反映した JSON を返してください。
event_type は {event_type} のまま。該当フィールドを更新した完全な JSON にしてください。
"""

_TYPE_RULES: dict[str, str] = {
    "meal": """フィールド: meal_type, items（name, quantity）
例: 修正指示「2杯だった」→ items の quantity を 2 に更新""",
    "batch_created": """フィールド: batch_name, servings""",
    "consumed": """フィールド: target, source, quantity_total, quantity_used, quantity_remaining""",
    "purchase": """フィールド: items""",
    "unknown": "分かる範囲で構造化してください。",
}


class MealRevisionResult(BaseModel):
    event_type: Literal["meal"] = "meal"
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    items: list[FoodItem] | None = None


class BatchRevisionResult(BaseModel):
    event_type: Literal["batch_created"] = "batch_created"
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    batch_name: str | None = None
    servings: int | None = None


class ConsumedRevisionResult(BaseModel):
    event_type: Literal["consumed"] = "consumed"
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    target: str | None = None
    source: str | None = None
    quantity_total: float | int | None = None
    quantity_used: float | int | None = None
    quantity_remaining: float | int | None = None


class PurchaseRevisionResult(BaseModel):
    event_type: Literal["purchase"] = "purchase"
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    items: list[FoodItem] | None = None


class GenericRevisionResult(BaseModel):
    event_type: Literal["meal", "batch_created", "consumed", "purchase", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    items: list[FoodItem] | None = None
    batch_name: str | None = None
    servings: int | None = None
    target: str | None = None
    source: str | None = None
    quantity_total: float | int | None = None
    quantity_used: float | int | None = None
    quantity_remaining: float | int | None = None


_REVISION_SCHEMAS: dict[str, type[BaseModel]] = {
    "meal": MealRevisionResult,
    "batch_created": BatchRevisionResult,
    "consumed": ConsumedRevisionResult,
    "purchase": PurchaseRevisionResult,
    "unknown": GenericRevisionResult,
}


def _instruction_requests_type_change(instruction: str) -> bool:
    text = instruction.strip()
    if not text:
        return False
    return any(marker in text for marker in _TYPE_CHANGE_MARKERS)


def _resolve_event_type(current: str, proposed: str, instruction: str) -> str:
    if proposed == current:
        return current
    if _instruction_requests_type_change(instruction):
        return proposed
    return current


def _merge_draft(current: dict, revised: dict, event_type: str) -> dict:
    allowed = fields_for_event_type(event_type)
    merged = strip_to_event_type(dict(current), event_type)

    for key in allowed:
        if key in revised and revised[key] is not None:
            merged[key] = revised[key]

    merged["event_type"] = event_type
    merged["confidence"] = revised.get("confidence", merged.get("confidence", 0.8))
    return merged


def _canonicalize(value: object) -> object:
    """比較用に数値・構造を正規化"""
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _parsed_json_changed(before: dict, after: dict) -> bool:
    return _canonicalize(before) != _canonicalize(after)


def _build_revision_prompt(
    *,
    event_type: str,
    draft_json: dict,
    instruction: str,
    raw_text: str,
    strict: bool = False,
) -> str:
    type_rules = _TYPE_RULES.get(event_type, _TYPE_RULES["unknown"])
    prompt = _REVISION_BASE.format(
        raw_text=raw_text.strip() or "（なし）",
        event_type=event_type,
        draft_json=json.dumps(draft_json, ensure_ascii=False, indent=2),
        instruction=instruction.strip(),
        type_rules=type_rules,
    )
    if strict:
        prompt += _STRICT_SUFFIX.format(event_type=event_type, instruction=instruction.strip())
    return prompt


def _call_revision_model(
    *,
    event_type: str,
    draft_json: dict,
    instruction: str,
    raw_text: str,
    strict: bool,
) -> dict:
    schema = _REVISION_SCHEMAS.get(event_type, GenericRevisionResult)
    prompt = _build_revision_prompt(
        event_type=event_type,
        draft_json=draft_json,
        instruction=instruction,
        raw_text=raw_text,
        strict=strict,
    )
    response_text = generate_content(prompt, response_schema=schema)
    raw_data = extract_json_from_text(response_text)
    try:
        model = schema.model_validate(raw_data)
        raw_data = model.model_dump(exclude_none=True)
    except Exception:
        pass
    return raw_data


def _apply_revision(
    *,
    event_type: str,
    draft_json: dict,
    instruction: str,
    raw_text: str,
    strict: bool,
) -> dict:
    current_type = event_type
    raw_data = _call_revision_model(
        event_type=current_type,
        draft_json=draft_json,
        instruction=instruction,
        raw_text=raw_text,
        strict=strict,
    )

    proposed_type = raw_data.get("event_type", current_type)
    if not isinstance(proposed_type, str):
        proposed_type = current_type
    final_type = _resolve_event_type(current_type, proposed_type, instruction)

    revised = strip_to_event_type(raw_data, final_type)
    merged = _merge_draft(draft_json, revised, final_type)
    return validate_food_parse_result(merged, raw_text=raw_text)


def revise_draft(
    *,
    event_type: str,
    draft_json: dict,
    instruction: str,
    raw_text: str = "",
) -> dict:
    inst = instruction.strip()
    if not inst:
        raise ValueError("修正指示が空です")

    before = dict(draft_json)
    result = _apply_revision(
        event_type=event_type,
        draft_json=draft_json,
        instruction=inst,
        raw_text=raw_text,
        strict=False,
    )

    if not _parsed_json_changed(before, result["parsed_json"]):
        result = _apply_revision(
            event_type=event_type,
            draft_json=draft_json,
            instruction=inst,
            raw_text=raw_text,
            strict=True,
        )

    if not _parsed_json_changed(before, result["parsed_json"]):
        raise ValueError(
            "修正を反映できませんでした。指示をもう少し具体的にするか、「再生成」をお試しください。"
        )

    return result
