"""parsed_json 内の items（内訳）の取り出し"""

import json
from typing import Any


def parse_parsed_json(parsed_json: str | dict) -> dict:
    if isinstance(parsed_json, dict):
        return parsed_json
    try:
        obj = json.loads(parsed_json)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def extract_items(parsed_json: str | dict) -> list[dict[str, Any]]:
    """items 配列を正規化して返す（各要素に index を付与）"""
    obj = parse_parsed_json(parsed_json)
    raw = obj.get("items")
    if not isinstance(raw, list):
        return []

    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        price = entry.get("price")
        items.append(
            {
                "index": index,
                "name": str(name).strip() if name is not None else "",
                "price": price,
            }
        )
    return items
