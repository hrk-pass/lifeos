"""FastAPI アプリケーションのエントリポイント"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine, get_db
from app.db_migrate import migrate_annotations_item_index
from app.models import (
    Annotation,
    BatchIngredient,
    Capture,
    DraftIngredientLink,
    EventDraft,
    ParsedEvent,
    PurchaseItem,
    QuickEvent,
    USER_CATEGORIES,
)
from app.parsed_items import extract_items
from app.routes import analyze, annotations, capture, drafts, quick_add
from app.services.draft_service import draft_to_dict
from app.services.purchase_item_service import (
    backfill_purchase_items,
    list_purchase_items,
    purchase_item_to_dict,
)

# テーブルがなければ作成（初回起動時）
Base.metadata.create_all(bind=engine)
migrate_annotations_item_index()

with SessionLocal() as _db:
    backfill_purchase_items(_db)

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


def _draft_linked_item_ids(db: Session, draft_ids: list[int]) -> dict[int, list[int]]:
    if not draft_ids:
        return {}
    stmt = select(DraftIngredientLink).where(DraftIngredientLink.draft_id.in_(draft_ids))
    rows = db.scalars(stmt).all()
    result: dict[int, list[int]] = {did: [] for did in draft_ids}
    for row in rows:
        result.setdefault(row.draft_id, []).append(row.purchase_item_id)
    return result


def _batch_ingredients_by_event(db: Session, event_ids: list[int]) -> dict[int, list[dict]]:
    if not event_ids:
        return {}
    stmt = (
        select(BatchIngredient, PurchaseItem)
        .join(PurchaseItem, BatchIngredient.purchase_item_id == PurchaseItem.id)
        .where(BatchIngredient.batch_event_id.in_(event_ids))
    )
    rows = db.execute(stmt).all()
    result: dict[int, list[dict]] = {eid: [] for eid in event_ids}
    for link, item in rows:
        result.setdefault(link.batch_event_id, []).append(
            {
                "purchase_item_id": item.id,
                "item_name": item.item_name,
                "price": item.price,
            }
        )
    return result


def _format_parsed_json_display(parsed_json: dict) -> str:
    """タイムライン用の parsed_json 表示文字列"""
    if not parsed_json:
        return "—"
    return json.dumps(parsed_json, ensure_ascii=False, indent=2)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    """下書き・確定イベント・キャプチャ・解析結果を新しい順に HTML で表示"""
    backfill_purchase_items(db)
    purchase_items_catalog = [
        purchase_item_to_dict(row) for row in list_purchase_items(db, limit=200)
    ]

    draft_stmt = select(EventDraft).order_by(EventDraft.updated_at.desc())
    draft_rows = db.scalars(draft_stmt).all()
    draft_ids = [row.id for row in draft_rows]
    draft_links = _draft_linked_item_ids(db, draft_ids)
    event_drafts = []
    for row in draft_rows:
        d = draft_to_dict(row)
        d["created_at_display"] = _format_datetime(row.created_at)
        d["updated_at_display"] = _format_datetime(row.updated_at)
        d["draft_json_display"] = _format_parsed_json_display(d["draft_json"])
        d["is_editable"] = row.status == "draft"
        d["linked_purchase_item_ids"] = draft_links.get(row.id, [])
        d["show_ingredient_linking"] = row.status == "draft" and row.event_type == "batch_created"
        event_drafts.append(d)

    quick_stmt = select(QuickEvent).order_by(QuickEvent.created_at.desc())
    quick_rows = db.scalars(quick_stmt).all()
    batch_event_ids = [row.id for row in quick_rows if row.event_type == "batch_created"]
    batch_ingredients_map = _batch_ingredients_by_event(db, batch_event_ids)
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
                "linked_ingredients": batch_ingredients_map.get(row.id, []),
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
            "purchase_items_catalog": purchase_items_catalog,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _truncate(text: str, max_len: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1] + "…"
