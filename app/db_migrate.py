"""SQLite 向けの軽量スキーマ移行（Alembic 未導入のため）"""

from sqlalchemy import inspect, text

from app.db import engine


def migrate_annotations_item_index() -> None:
    """annotations に item_index を追加し、(parsed_event_id, item_index) で一意にする"""
    insp = inspect(engine)
    if "annotations" not in insp.get_table_names():
        return

    columns = {col["name"] for col in insp.get_columns("annotations")}

    with engine.begin() as conn:
        if "item_index" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE annotations "
                    "ADD COLUMN item_index INTEGER NOT NULL DEFAULT 0"
                )
            )
        # 旧: parsed_event_id 単独 UNIQUE → 内訳ごとに複数行を許可
        conn.execute(text("DROP INDEX IF EXISTS ix_annotations_parsed_event_id"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_annotations_event_item "
                "ON annotations (parsed_event_id, item_index)"
            )
        )
