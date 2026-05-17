"""人間注釈用 API（AI 推定 parsed_events / items とは分離）"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Annotation, ParsedEvent, USER_CATEGORY_SET
from app.parsed_items import extract_items
from app.schemas import AnnotationCreate, AnnotationSuccess

router = APIRouter(tags=["annotations"])


def _require_valid_item_index(parsed_event: ParsedEvent, item_index: int) -> list[dict]:
    if item_index < 0:
        raise HTTPException(status_code=422, detail="item_index は 0 以上にしてください")
    items = extract_items(parsed_event.parsed_json)
    if not items:
        raise HTTPException(
            status_code=422,
            detail="この解析結果に内訳（items）がありません",
        )
    if item_index >= len(items):
        raise HTTPException(
            status_code=422,
            detail=f"item_index は 0〜{len(items) - 1} の範囲で指定してください",
        )
    return items


@router.post(
    "/annotations/{parsed_event_id}/{item_index}",
    response_model=AnnotationSuccess,
)
def upsert_annotation(
    parsed_event_id: int,
    item_index: int,
    body: AnnotationCreate,
    db: Session = Depends(get_db),
):
    """内訳（items[item_index]）に対する人間のカテゴリ・メモを保存（既存なら更新）"""
    parsed_event = db.get(ParsedEvent, parsed_event_id)
    if parsed_event is None:
        raise HTTPException(status_code=404, detail="解析イベントが見つかりません")

    _require_valid_item_index(parsed_event, item_index)

    if body.user_category is not None and body.user_category not in USER_CATEGORY_SET:
        raise HTTPException(
            status_code=422,
            detail=f"user_category は {sorted(USER_CATEGORY_SET)} のいずれかにしてください",
        )

    stmt = select(Annotation).where(
        Annotation.parsed_event_id == parsed_event_id,
        Annotation.item_index == item_index,
    )
    record = db.scalar(stmt)
    if record is None:
        record = Annotation(parsed_event_id=parsed_event_id, item_index=item_index)
        db.add(record)

    record.user_category = body.user_category or None
    record.memo = body.memo or None
    record.tags_json = body.tags_json

    db.commit()
    db.refresh(record)

    return AnnotationSuccess(
        success=True,
        parsed_event_id=parsed_event_id,
        item_index=item_index,
        user_category=record.user_category,
        memo=record.memo,
        tags_json=record.tags_json,
    )
