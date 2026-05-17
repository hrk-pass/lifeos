"""データベーステーブル定義"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

EVENT_TYPES = frozenset({"purchase", "inventory", "food", "unknown"})

FOOD_EVENT_TYPES = frozenset({
    "meal",
    "batch_created",
    "consumed",
    "purchase",
    "unknown",
})

DRAFT_STATUSES = frozenset({"draft", "confirmed", "discarded"})

USER_CATEGORIES = (
    "food",
    "daily",
    "utility",
    "transport",
    "medical",
    "work",
    "hobby",
    "other",
)
USER_CATEGORY_SET = frozenset(USER_CATEGORIES)


def utc_now() -> datetime:
    """保存時刻（UTC）"""
    return datetime.now(timezone.utc)


class Capture(Base):
    """OCR などから送られた文字列を保存するテーブル"""

    __tablename__ = "captures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    parsed_events: Mapped[list["ParsedEvent"]] = relationship(
        "ParsedEvent",
        back_populates="capture",
        order_by="ParsedEvent.created_at.desc()",
    )


class ParsedEvent(Base):
    """AI による構造化イベント（captures の解釈結果。再解析で複数行になり得る）"""

    __tablename__ = "parsed_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("captures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parsed_json: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    capture: Mapped["Capture"] = relationship("Capture", back_populates="parsed_events")
    annotations: Mapped[list["Annotation"]] = relationship(
        "Annotation",
        back_populates="parsed_event",
        order_by="Annotation.item_index",
    )


class Annotation(Base):
    """人間によるカテゴリ・メモ（parsed_json.items の内訳ごと。AI 推定とは分離）"""

    __tablename__ = "annotations"
    __table_args__ = (
        UniqueConstraint("parsed_event_id", "item_index", name="uq_annotations_event_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parsed_event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("parsed_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    parsed_event: Mapped["ParsedEvent"] = relationship(
        "ParsedEvent", back_populates="annotations"
    )


class EventDraft(Base):
    """AI 生成の下書き（人間レビュー後に quick_events へ Commit）"""

    __tablename__ = "event_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    draft_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class QuickEvent(Base):
    """Commit 済みの確定イベント（raw_text 不変）"""

    __tablename__ = "quick_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    batch_ingredients: Mapped[list["BatchIngredient"]] = relationship(
        "BatchIngredient",
        back_populates="batch_event",
        cascade="all, delete-orphan",
    )


class PurchaseItem(Base):
    """購入イベント内の商品（parsed_events から抽出。Batch 紐付けの単位）"""

    __tablename__ = "purchase_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parsed_event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("parsed_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_name: Mapped[str] = mapped_column(String(256), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    parsed_event: Mapped["ParsedEvent"] = relationship("ParsedEvent")
    batch_links: Mapped[list["BatchIngredient"]] = relationship(
        "BatchIngredient",
        back_populates="purchase_item",
    )


class BatchIngredient(Base):
    """確定 Batch（quick_events）と購入商品の関連（数量管理なし）"""

    __tablename__ = "batch_ingredients"
    __table_args__ = (
        UniqueConstraint("batch_event_id", "purchase_item_id", name="uq_batch_purchase_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("quick_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purchase_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("purchase_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    batch_event: Mapped["QuickEvent"] = relationship(
        "QuickEvent", back_populates="batch_ingredients"
    )
    purchase_item: Mapped["PurchaseItem"] = relationship(
        "PurchaseItem", back_populates="batch_links"
    )


class DraftIngredientLink(Base):
    """下書き段階の材料紐付け（Commit 時に batch_ingredients へ確定）"""

    __tablename__ = "draft_ingredient_links"
    __table_args__ = (
        UniqueConstraint("draft_id", "purchase_item_id", name="uq_draft_purchase_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("event_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purchase_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("purchase_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
