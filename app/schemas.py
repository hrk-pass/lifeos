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
