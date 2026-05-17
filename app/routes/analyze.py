"""AI 解析用 API"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Capture, ParsedEvent
from app.schemas import AnalyzeSuccess, ParsedEventData
from app.services.ai_parser import parse_capture_text

router = APIRouter(tags=["analyze"])


@router.post("/analyze/{capture_id}", response_model=AnalyzeSuccess)
def analyze_capture(capture_id: int, db: Session = Depends(get_db)):
    """
    指定キャプチャの raw_text を Gemini で解析し、parsed_events に保存する。
    captures.raw_text は変更しない。
    """
    capture = db.get(Capture, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="キャプチャが見つかりません")

    try:
        result = parse_capture_text(capture.raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    record = ParsedEvent(
        capture_id=capture.id,
        event_type=result["event_type"],
        parsed_json=json.dumps(result["parsed_json"], ensure_ascii=False),
        confidence=result["confidence"],
    )
    db.add(record)
    db.commit()

    return AnalyzeSuccess(
        success=True,
        event=ParsedEventData(
            event_type=result["event_type"],
            parsed_json=result["parsed_json"],
        ),
    )
