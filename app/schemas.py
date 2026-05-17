"""API のリクエスト / レスポンス型"""

from pydantic import BaseModel, Field


class CaptureCreate(BaseModel):
    """POST /capture のリクエストボディ"""

    text: str = Field(..., min_length=1, description="OCR で取得した文字列")
    source: str = Field(default="iphone", description="送信元（例: iphone）")


class CaptureSuccess(BaseModel):
    """POST /capture のレスポンス"""

    success: bool = True


class ParsedEventData(BaseModel):
    """AI 解析結果のイベント部分"""

    event_type: str
    parsed_json: dict


class AnalyzeSuccess(BaseModel):
    """POST /analyze/{capture_id} のレスポンス"""

    success: bool = True
    event: ParsedEventData


class AnnotationCreate(BaseModel):
    """POST /annotations/{parsed_event_id}/{item_index} のリクエストボディ"""

    user_category: str | None = Field(
        default=None, description="人間が付与したカテゴリ"
    )
    memo: str | None = Field(default=None, description="自由メモ")
    tags_json: str = Field(default="[]", description="タグ JSON 文字列")


class AnnotationSuccess(BaseModel):
    """POST /annotations/{parsed_event_id}/{item_index} のレスポンス"""

    success: bool = True
    parsed_event_id: int
    item_index: int
    user_category: str | None = None
    memo: str | None = None
    tags_json: str = "[]"
