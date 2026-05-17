"""キャプチャ（文字列保存）用 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Capture
from app.schemas import CaptureCreate, CaptureSuccess

router = APIRouter(tags=["capture"])


@router.post("/capture", response_model=CaptureSuccess)
def create_capture(body: CaptureCreate, db: Session = Depends(get_db)):
    """
    iPhone ショートカットなどから文字列を受け取り、そのまま DB に保存する。
    AI 解析は行わない。
    """
    record = Capture(
        raw_text=body.text,
        source=body.source,
    )
    db.add(record)
    db.commit()
    return CaptureSuccess(success=True)
