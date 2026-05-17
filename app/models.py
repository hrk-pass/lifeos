"""データベーステーブル定義"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

EVENT_TYPES = frozenset({"purchase", "inventory", "food", "unknown"})


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
