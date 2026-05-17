"""自然文クイック入力 API（後方互換: Draft 作成へ委譲）"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import DraftResponse, QuickAddRequest
from app.services.draft_service import create_draft_from_text, draft_to_dict

router = APIRouter(tags=["quick-add"])


@router.post("/quick-add", response_model=DraftResponse)
def quick_add(body: QuickAddRequest, db: Session = Depends(get_db)):
    """
    自然文から AI 下書きを作成する（POST /drafts と同じ）。
    確定には POST /drafts/{id}/commit が必要。
    """
    try:
        draft = create_draft_from_text(db, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return DraftResponse(**draft_to_dict(draft))
