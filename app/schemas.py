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


class QuickAddRequest(BaseModel):
    """POST /quick-add / POST /drafts のリクエストボディ"""

    text: str = Field(..., min_length=1, description="自然文（例: 昼 カレー）")


class DraftCreateRequest(BaseModel):
    """POST /drafts のリクエストボディ"""

    text: str = Field(..., min_length=1, description="自然文（例: 夜 カレー）")


class DraftReviseRequest(BaseModel):
    """POST /drafts/{draft_id}/revise のリクエストボディ"""

    instruction: str = Field(..., min_length=1, description="修正指示（例: 2杯だった）")


class DraftResponse(BaseModel):
    """Draft の API レスポンス"""

    id: int
    raw_text: str
    event_type: str
    draft_json: dict
    confidence: float
    status: str


class DraftSuccess(BaseModel):
    """Commit / Discard のレスポンス"""

    success: bool = True
    draft_id: int
    quick_event_id: int | None = None
    status: str
