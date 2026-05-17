"""Batch 下書き向けの購入材料候補提案（AI は候補のみ。確定は DB）"""

from pydantic import BaseModel, Field

from app.services.ai.client import extract_json_from_text, generate_content

_PROMPT_TEMPLATE = """あなたは料理の材料候補を提案するアシスタントです。
在庫の真実は持たず、下書き内容と購入履歴から「使いそうな材料名」だけを返してください。

## 下書き（Batch / Meal など）
{draft_json}

## 購入済み商品名一覧（このリスト以外は絶対に含めない）
{purchase_item_names}

## ルール
- candidate_ingredients の各要素は、上記「購入済み商品名一覧」のいずれかと**完全一致**する文字列にすること
- 料理名・下書きの文脈から妥当なものだけ選ぶ（例: カレー → 鶏肉・玉ねぎ・カレールー）
- 曖昧一致は**購入リスト内の既存名称を選ぶ**意味でのみ可（例: 下書きが「ルー」でリストに「カレールー」がある → 「カレールー」を返す）
- 推測しすぎない。関連が弱いもの（牛乳・ヨーグルトなど）は含めない
- 該当なしなら空配列

返答は JSON のみ。説明文やマークダウンは付けないでください。

出力例:
{{
  "candidate_ingredients": ["鶏肉", "玉ねぎ", "カレールー"]
}}
"""


class IngredientCandidateResult(BaseModel):
    candidate_ingredients: list[str] = Field(default_factory=list)


def suggest_ingredient_candidates(
    *,
    draft_json: dict,
    purchase_item_names: list[str],
) -> list[str]:
    """Gemini で材料候補名を返す（purchase_item_names の部分集合）"""
    import json

    names = [n.strip() for n in purchase_item_names if n and str(n).strip()]
    if not names:
        return []

    prompt = _PROMPT_TEMPLATE.format(
        draft_json=json.dumps(draft_json, ensure_ascii=False, indent=2),
        purchase_item_names="\n".join(f"- {n}" for n in names),
    )
    response_text = generate_content(prompt, response_schema=IngredientCandidateResult)
    raw = extract_json_from_text(response_text)

    try:
        model = IngredientCandidateResult.model_validate(raw)
        candidates = model.candidate_ingredients
    except Exception:
        raw_list = raw.get("candidate_ingredients", [])
        candidates = raw_list if isinstance(raw_list, list) else []

    name_set = set(names)
    result: list[str] = []
    seen: set[str] = set()
    for entry in candidates:
        if not isinstance(entry, str):
            continue
        name = entry.strip()
        if not name or name not in name_set or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result
