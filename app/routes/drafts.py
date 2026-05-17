"""Draft / Review / Commit ワークフロー API"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import EventDraft, QuickEvent, utc_now
from app.schemas import (
    DraftCreateRequest,
    DraftResponse,
    DraftReviseRequest,
    DraftSuccess,
    IngredientCandidatesResponse,
    LinkIngredientsRequest,
    LinkIngredientsResponse,
    PurchaseItemOut,
)
from app.services.ingredient_service import (
    build_ingredient_candidates,
    commit_batch_ingredients,
    get_linked_purchase_item_ids,
    link_draft_ingredients,
)
from app.services.ai.draft_revision_parser import revise_draft
from app.services.ai.food_event_parser import parse_food_event_text
from app.services.draft_service import (
    apply_parse_result,
    create_draft_from_text,
    draft_to_dict,
    parse_draft_json,
)

router = APIRouter(tags=["drafts"])


def draft_response(draft: EventDraft) -> DraftResponse:
    return DraftResponse(**draft_to_dict(draft))


def _get_draft_or_404(draft_id: int, db: Session) -> EventDraft:
    draft = db.get(EventDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="下書きが見つかりません")
    return draft


def _require_status(draft: EventDraft, expected: str = "draft") -> None:
    if draft.status != expected:
        raise HTTPException(
            status_code=409,
            detail=f"この操作は status={expected} のときのみ可能です（現在: {draft.status}）",
        )


@router.post("/drafts", response_model=DraftResponse)
def create_draft(body: DraftCreateRequest, db: Session = Depends(get_db)):
    """自然文から AI 下書きを生成する（quick_events には保存しない）"""
    try:
        draft = create_draft_from_text(db, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return draft_response(draft)


@router.post("/drafts/{draft_id}/revise", response_model=DraftResponse)
def revise_draft_endpoint(
    draft_id: int,
    body: DraftReviseRequest,
    db: Session = Depends(get_db),
):
    """自然文指示で下書き JSON を修正する"""
    draft = _get_draft_or_404(draft_id, db)
    _require_status(draft)

    current_json = parse_draft_json(draft)

    try:
        result = revise_draft(
            event_type=draft.event_type,
            draft_json=current_json,
            instruction=body.instruction,
            raw_text=draft.raw_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    apply_parse_result(draft, result)
    db.commit()
    db.refresh(draft)

    return draft_response(draft)


@router.post("/drafts/{draft_id}/regenerate", response_model=DraftResponse)
def regenerate_draft(draft_id: int, db: Session = Depends(get_db)):
    """raw_text から下書きを再生成する"""
    draft = _get_draft_or_404(draft_id, db)
    _require_status(draft)

    try:
        result = parse_food_event_text(draft.raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    apply_parse_result(draft, result)
    db.commit()
    db.refresh(draft)

    return draft_response(draft)


@router.post("/drafts/{draft_id}/ingredient-candidates", response_model=IngredientCandidatesResponse)
def ingredient_candidates(draft_id: int, db: Session = Depends(get_db)):
    """下書きと過去の購入商品から材料候補を AI 提案する"""
    draft = _get_draft_or_404(draft_id, db)
    _require_status(draft)

    if draft.event_type != "batch_created":
        raise HTTPException(
            status_code=422,
            detail="材料候補は event_type=batch_created の下書きのみ利用できます",
        )

    try:
        result = build_ingredient_candidates(db, draft)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return IngredientCandidatesResponse(
        candidate_ingredients=result["candidate_ingredients"],
        purchase_items=[PurchaseItemOut(**row) for row in result["purchase_items"]],
        linked_purchase_item_ids=result["linked_purchase_item_ids"],
    )


@router.post("/drafts/{draft_id}/link-ingredients", response_model=LinkIngredientsResponse)
def link_ingredients(
    draft_id: int,
    body: LinkIngredientsRequest,
    db: Session = Depends(get_db),
):
    """選択した purchase_items を下書きに紐付ける（DB が真実）"""
    draft = _get_draft_or_404(draft_id, db)
    _require_status(draft)

    if draft.event_type != "batch_created":
        raise HTTPException(
            status_code=422,
            detail="材料紐付けは event_type=batch_created の下書きのみ利用できます",
        )

    try:
        count = link_draft_ingredients(db, draft, body.purchase_item_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    draft.updated_at = utc_now()
    db.commit()

    linked_ids = get_linked_purchase_item_ids(db, draft.id)
    return LinkIngredientsResponse(
        success=True,
        draft_id=draft.id,
        linked_count=count,
        linked_purchase_item_ids=linked_ids,
    )


@router.post("/drafts/{draft_id}/commit", response_model=DraftSuccess)
def commit_draft(draft_id: int, db: Session = Depends(get_db)):
    """下書きを確定し quick_events に保存する"""
    draft = _get_draft_or_404(draft_id, db)
    _require_status(draft)

    draft_json = parse_draft_json(draft)
    event = QuickEvent(
        raw_text=draft.raw_text,
        event_type=draft.event_type,
        parsed_json=json.dumps(draft_json, ensure_ascii=False),
        confidence=draft.confidence,
    )
    db.add(event)
    db.flush()

    linked_count = commit_batch_ingredients(db, draft=draft, batch_event=event)

    draft.status = "confirmed"
    draft.updated_at = utc_now()
    db.commit()
    db.refresh(event)
    db.refresh(draft)

    return DraftSuccess(
        success=True,
        draft_id=draft.id,
        quick_event_id=event.id,
        status=draft.status,
        linked_ingredient_count=linked_count if linked_count else None,
    )


@router.post("/drafts/{draft_id}/discard", response_model=DraftSuccess)
def discard_draft(draft_id: int, db: Session = Depends(get_db)):
    """下書きを破棄（レコードは残す）"""
    draft = _get_draft_or_404(draft_id, db)
    _require_status(draft)

    draft.status = "discarded"
    draft.updated_at = utc_now()
    db.commit()
    db.refresh(draft)

    return DraftSuccess(
        success=True,
        draft_id=draft.id,
        quick_event_id=None,
        status=draft.status,
    )
