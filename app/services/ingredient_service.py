"""材料紐付け（下書き → Commit 時の batch_ingredients）"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import BatchIngredient, DraftIngredientLink, EventDraft, PurchaseItem, QuickEvent
from app.services.ai.ingredient_candidate_parser import suggest_ingredient_candidates
from app.services.draft_service import parse_draft_json
from app.services.purchase_item_service import list_purchase_items, purchase_item_to_dict


def get_linked_purchase_item_ids(db: Session, draft_id: int) -> list[int]:
    stmt = select(DraftIngredientLink.purchase_item_id).where(
        DraftIngredientLink.draft_id == draft_id
    )
    return list(db.scalars(stmt).all())


def link_draft_ingredients(db: Session, draft: EventDraft, purchase_item_ids: list[int]) -> int:
    """下書きの材料リンクを置き換える。戻り値はリンク件数。"""
    unique_ids = sorted({int(i) for i in purchase_item_ids if i is not None})
    if not unique_ids:
        db.execute(
            delete(DraftIngredientLink).where(DraftIngredientLink.draft_id == draft.id)
        )
        return 0

    existing = db.scalars(
        select(PurchaseItem.id).where(PurchaseItem.id.in_(unique_ids))
    ).all()
    existing_set = set(existing)
    missing = [i for i in unique_ids if i not in existing_set]
    if missing:
        raise ValueError(f"存在しない purchase_item_id: {missing}")

    db.execute(delete(DraftIngredientLink).where(DraftIngredientLink.draft_id == draft.id))
    for pid in unique_ids:
        db.add(DraftIngredientLink(draft_id=draft.id, purchase_item_id=pid))
    db.flush()
    return len(unique_ids)


def build_ingredient_candidates(db: Session, draft: EventDraft) -> dict:
    """AI 候補 + 購入商品一覧 + 現在のリンク ID"""
    items = list_purchase_items(db)
    purchase_items = [purchase_item_to_dict(row) for row in items]
    names = [row["item_name"] for row in purchase_items]

    draft_json = parse_draft_json(draft)
    candidates = suggest_ingredient_candidates(
        draft_json=draft_json,
        purchase_item_names=names,
    )

    return {
        "candidate_ingredients": candidates,
        "purchase_items": purchase_items,
        "linked_purchase_item_ids": get_linked_purchase_item_ids(db, draft.id),
    }


def commit_batch_ingredients(
    db: Session,
    *,
    draft: EventDraft,
    batch_event: QuickEvent,
) -> int:
    """draft_ingredient_links を batch_ingredients にコピーする"""
    if draft.event_type != "batch_created":
        return 0

    link_ids = get_linked_purchase_item_ids(db, draft.id)
    if not link_ids:
        return 0

    count = 0
    for pid in link_ids:
        db.add(
            BatchIngredient(
                batch_event_id=batch_event.id,
                purchase_item_id=pid,
            )
        )
        count += 1
    db.flush()
    return count
