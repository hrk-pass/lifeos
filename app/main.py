"""FastAPI アプリケーションのエントリポイント"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.models import Capture, ParsedEvent
from app.routes import analyze, capture

# テーブルがなければ作成（初回起動時）
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="LifeOS",
    description="生活イベント収集 — OCR 文字列の保存と AI による構造化解析",
)

# API ルートを登録
app.include_router(capture.router)
app.include_router(analyze.router)

# Jinja2 テンプレート（app/templates/）
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 一覧表示用のタイムゾーン（日本時間）
JST = ZoneInfo("Asia/Tokyo")


@app.get("/health")
def health():
    """サーバー稼働確認用"""
    return {"status": "ok"}


def _format_datetime(dt) -> str:
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")


def _latest_parsed_by_capture(db: Session) -> dict[int, ParsedEvent]:
    """各 capture_id ごとに最新の parsed_event を返す"""
    latest_ids = (
        select(func.max(ParsedEvent.id).label("max_id"))
        .group_by(ParsedEvent.capture_id)
        .subquery()
    )
    stmt = select(ParsedEvent).join(latest_ids, ParsedEvent.id == latest_ids.c.max_id)
    rows = db.scalars(stmt).all()
    return {row.capture_id: row for row in rows}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    """保存済みキャプチャと最新の AI 解析結果を新しい順に HTML で表示"""
    stmt = select(Capture).order_by(Capture.created_at.desc())
    rows = db.scalars(stmt).all()
    latest_parsed = _latest_parsed_by_capture(db)

    captures = []
    for row in rows:
        parsed = latest_parsed.get(row.id)
        parsed_data = None
        if parsed is not None:
            try:
                parsed_obj = json.loads(parsed.parsed_json)
            except json.JSONDecodeError:
                parsed_obj = {"_error": "invalid_json"}
            parsed_data = {
                "event_type": parsed.event_type,
                "parsed_json": parsed_obj,
                "parsed_json_display": json.dumps(
                    parsed_obj, ensure_ascii=False, indent=2
                ),
                "confidence": parsed.confidence,
                "analyzed_at_display": _format_datetime(parsed.created_at),
            }

        captures.append(
            {
                "id": row.id,
                "raw_text": row.raw_text,
                "source": row.source,
                "created_at_display": _format_datetime(row.created_at),
                "is_analyzed": parsed is not None,
                "parsed": parsed_data,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"captures": captures},
    )
