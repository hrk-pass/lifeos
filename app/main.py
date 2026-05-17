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
from app.db_migrate import migrate_annotations_item_index
from app.models import Annotation, Capture, EventDraft, ParsedEvent, QuickEvent, USER_CATEGORIES
from app.parsed_items import extract_items
from app.routes import analyze, annotations, capture, drafts, quick_add
from app.services.draft_service import draft_to_dict

# テーブルがなければ作成（初回起動時）
Base.metadata.create_all(bind=engine)
migrate_annotations_item_index()

app = FastAPI(
    title="LifeOS",
    description="生活イベント収集 — OCR 文字列の保存と AI による構造化解析",
)

# API ルートを登録
app.include_router(capture.router)
app.include_router(analyze.router)
app.include_router(annotations.router)
app.include_router(quick_add.router)
app.include_router(drafts.router)

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


def _annotations_by_parsed_event(
    db: Session, parsed_event_ids: list[int]
) -> dict[tuple[int, int], Annotation]:
    """(parsed_event_id, item_index) → Annotation"""
    if not parsed_event_ids:
        return {}
    stmt = select(Annotation).where(Annotation.parsed_event_id.in_(parsed_event_ids))
    rows = db.scalars(stmt).all()
    return {(row.parsed_event_id, row.item_index): row for row in rows}


def _format_parsed_json_display(parsed_json: dict) -> str:
    """タイムライン用の parsed_json 表示文字列"""
    if not parsed_json:
        return "—"
    return json.dumps(parsed_json, ensure_ascii=False, indent=2)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    """下書き・確定イベント・キャプチャ・解析結果を新しい順に HTML で表示"""
    draft_stmt = select(EventDraft).order_by(EventDraft.updated_at.desc())
    draft_rows = db.scalars(draft_stmt).all()
    event_drafts = []
    for row in draft_rows:
        d = draft_to_dict(row)
        d["created_at_display"] = _format_datetime(row.created_at)
        d["updated_at_display"] = _format_datetime(row.updated_at)
        d["draft_json_display"] = _format_parsed_json_display(d["draft_json"])
        d["is_editable"] = row.status == "draft"
        event_drafts.append(d)

    quick_stmt = select(QuickEvent).order_by(QuickEvent.created_at.desc())
    quick_rows = db.scalars(quick_stmt).all()
    quick_events = []
    for row in quick_rows:
        try:
            parsed_obj = json.loads(row.parsed_json)
        except json.JSONDecodeError:
            parsed_obj = {}
        quick_events.append(
            {
                "id": row.id,
                "raw_text": row.raw_text,
                "event_type": row.event_type,
                "confidence": row.confidence,
                "parsed_json": parsed_obj,
                "parsed_json_display": _format_parsed_json_display(parsed_obj),
                "created_at_display": _format_datetime(row.created_at),
            }
        )

    stmt = select(Capture).order_by(Capture.created_at.desc())
    rows = db.scalars(stmt).all()
    latest_parsed = _latest_parsed_by_capture(db)
    parsed_ids = [p.id for p in latest_parsed.values()]
    annotations_map = _annotations_by_parsed_event(db, parsed_ids)

    captures = []
    analyzed_rows = []

    for row in rows:
        parsed = latest_parsed.get(row.id)
        captures.append(
            {
                "id": row.id,
                "raw_text": row.raw_text,
                "raw_preview": _truncate(row.raw_text, 80),
                "source": row.source,
                "created_at_display": _format_datetime(row.created_at),
                "is_analyzed": parsed is not None,
            }
        )

        if parsed is None:
            continue

        try:
            parsed_obj = json.loads(parsed.parsed_json)
        except json.JSONDecodeError:
            parsed_obj = {}

        store = parsed_obj.get("store")
        store_display = str(store).strip() if store else ""

        for item in extract_items(parsed_obj):
            idx = item["index"]
            ann = annotations_map.get((parsed.id, idx))
            analyzed_rows.append(
                {
                    "parsed_event_id": parsed.id,
                    "item_index": idx,
                    "capture_id": row.id,
                    "created_at_display": _format_datetime(row.created_at),
                    "event_type": parsed.event_type,
                    "store": store_display,
                    "item_name": item["name"] or "（名称なし）",
                    "item_price": item["price"],
                    "user_category": ann.user_category if ann else None,
                    "memo": (ann.memo or "") if ann else "",
                }
            )

    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "event_drafts": event_drafts,
            "quick_events": quick_events,
            "captures": captures,
            "analyzed_rows": analyzed_rows,
            "user_categories": USER_CATEGORIES,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _truncate(text: str, max_len: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1] + "…"
