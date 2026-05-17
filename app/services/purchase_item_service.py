"""parsed_events の purchase 内訳から purchase_items を生成する"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ParsedEvent, PurchaseItem
from app.parsed_items import extract_items, parse_parsed_json


def sync_purchase_items_from_parsed(
    db: Session,
    *,
    parsed_event_id: int,
    parsed_json: dict,
    event_type: str,
) -> list[PurchaseItem]:
    """purchase イベントの items を purchase_items 行として保存する"""
    if event_type != "purchase":
        return []

    obj = parse_parsed_json(parsed_json)
    rows: list[PurchaseItem] = []
    for item in extract_items(obj):
        name = (item.get("name") or "").strip()
        if not name:
            continue
        price = item.get("price")
        try:
            price_val = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_val = None

        row = PurchaseItem(
            parsed_event_id=parsed_event_id,
            item_name=name,
            price=price_val,
            quantity=None,
        )
        db.add(row)
        rows.append(row)

    if rows:
        db.flush()
    return rows


def backfill_purchase_items(db: Session) -> int:
    """既存の purchase 解析から purchase_items を補完（機能追加前のデータ用）"""
    existing_ids = set(db.scalars(select(PurchaseItem.parsed_event_id).distinct()).all())
    stmt = select(ParsedEvent).where(ParsedEvent.event_type == "purchase")
    if existing_ids:
        stmt = stmt.where(ParsedEvent.id.not_in(existing_ids))

    created = 0
    for pe in db.scalars(stmt).all():
        try:
            parsed_obj = json.loads(pe.parsed_json)
        except json.JSONDecodeError:
            parsed_obj = {}
        if not isinstance(parsed_obj, dict):
            parsed_obj = {}

        rows = sync_purchase_items_from_parsed(
            db,
            parsed_event_id=pe.id,
            parsed_json=parsed_obj,
            event_type=pe.event_type,
        )
        created += len(rows)

    if created:
        db.commit()
    return created


def list_purchase_items(db: Session, *, limit: int = 200) -> list[PurchaseItem]:
    """紐付け候補用に直近の購入商品を返す（新しい順）"""
    stmt = (
        select(PurchaseItem)
        .order_by(PurchaseItem.created_at.desc(), PurchaseItem.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def purchase_item_to_dict(row: PurchaseItem) -> dict:
    return {
        "id": row.id,
        "parsed_event_id": row.parsed_event_id,
        "item_name": row.item_name,
        "price": row.price,
        "quantity": row.quantity,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
