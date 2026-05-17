"""データベース接続の設定"""

from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
import os

# プロジェクトルート（lifeos/）を基準に .env を読み込む
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# デフォルトは data/lifeos.db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/lifeos.db")

# SQLite ファイルのディレクトリを自動作成
if DATABASE_URL.startswith("sqlite:///./"):
    db_path = PROJECT_ROOT / DATABASE_URL.replace("sqlite:///./", "")
    db_path.parent.mkdir(parents=True, exist_ok=True)

# check_same_thread=False は FastAPI の複数リクエスト用
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy のモデル基底クラス"""
    pass


def get_db():
    """リクエストごとに DB セッションを開き、終了時に閉じる"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
