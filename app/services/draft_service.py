"""Draft 作成・更新の共通ロジック"""

import json

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import EventDraft, utc_now
from app.services.ai.food_event_parser import parse_food_event_text


def apply_parse_result(draft: EventDraft, result: dict) -> None:
    draft.event_type = result["event_type"]
    draft.draft_json = json.dumps(result["parsed_json"], ensure_ascii=False)
    draft.confidence = result["confidence"]
    draft.updated_at = utc_now()
    flag_modified(draft, "draft_json")


def create_draft_from_text(db: Session, text: str) -> EventDraft:
    """raw_text を保存し、AI で下書きを生成する"""
    raw = text.strip()
    if not raw:
        raise ValueError("text が空です")

    draft = EventDraft(raw_text=raw, status="draft")
    db.add(draft)
    db.commit()
    db.refresh(draft)

    result = parse_food_event_text(raw)
    apply_parse_result(draft, result)
    db.commit()
    db.refresh(draft)
    return draft


def parse_draft_json(draft: EventDraft) -> dict:
    try:
        obj = json.loads(draft.draft_json)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def draft_to_dict(draft: EventDraft) -> dict:
    """API / テンプレート用の下書き dict"""
    return {
        "id": draft.id,
        "raw_text": draft.raw_text,
        "event_type": draft.event_type,
        "draft_json": parse_draft_json(draft),
        "confidence": draft.confidence,
        "status": draft.status,
    }
