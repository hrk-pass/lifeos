# LifeOS — 生活イベント収集 MVP

家計簿ではなく、自然文や OCR 文字列を**そのまま**保存し、Gemini API で**構造化イベント**に変換する最小基盤です。

- **event_drafts**: AI 下書き（人間レビュー前。mutable）
- **quick_events**: Commit 済みの確定イベント（immutable raw_text）
- **captures**: 生の OCR 文字列（不変の生データ）
- **parsed_events**: AI による解釈結果（再解析可能）
- **purchase_items**: 購入イベント内の商品行（Batch / Meal 紐付けの単位）
- **batch_ingredients**: 確定 Batch と purchase_items の関連（数量なし）
- **draft_ingredient_links**: 下書き段階の材料選択（Commit 時に batch_ingredients へ）

## ディレクトリ構成

```
lifeos/
├── app/
│   ├── main.py              # FastAPI 起動・一覧・ルート登録
│   ├── db.py                # SQLite 接続
│   ├── models.py            # event_drafts / quick_events / captures
│   ├── schemas.py           # API の型定義
│   ├── routes/
│   │   ├── drafts.py        # Draft / Revise / Commit / Ingredient Linking
│   │   ├── quick_add.py     # POST /quick-add（Draft 作成へ委譲）
│   │   ├── capture.py       # POST /capture
│   │   └── analyze.py       # POST /analyze/{capture_id}
│   ├── services/
│   │   ├── draft_service.py
│   │   ├── purchase_item_service.py
│   │   ├── ingredient_service.py
│   │   ├── ai/
│   │   │   ├── client.py
│   │   │   ├── food_event_parser.py
│   │   │   ├── draft_revision_parser.py
│   │   │   └── ingredient_candidate_parser.py
│   │   └── ai_parser.py     # OCR / レシート解析
│   └── templates/
│       └── index.html       # 一覧 HTML（クイック入力・タイムライン）
├── data/
│   └── lifeos.db            # SQLite（初回起動で自動作成）
├── requirements.txt
├── .env
└── README.md
```

## セットアップ

### 1. プロジェクトへ移動

```bash
cd lifeos
```

### 2. 仮想環境を作成・有効化

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. 依存パッケージをインストール

```bash
pip install -r requirements.txt
```

### 4. Gemini API キーを設定

[Google AI Studio](https://aistudio.google.com/apikey) で API キーを取得し、`.env` に設定します。

```env
GEMINI_API_KEY=あなたのAPIキー
```

コスト重視の場合、デフォルトで `gemini-2.5-flash` を使用します（失敗時は `gemini-2.0-flash` → `gemini-2.5-flash-lite` にフォールバック）。  
モデルを変える場合:

```env
GEMINI_MODEL=gemini-2.0-flash
```

### 5. サーバー起動

プロジェクトルート（`lifeos/`）で実行してください。

**Mac のブラウザだけ使う場合**（localhost のみ）:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**iPhone ショートカットから POST する場合**（同一 Wi‑Fi 必須）:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- ヘルスチェック（Mac）: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- 一覧画面（Mac）: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- iPhone 用 POST URL の例: `http://192.168.0.73:8000/capture`（IP は環境により異なります）

## Draft Workflow（自然文 → レビュー → Commit）

AI 出力は**候補（下書き）**として保存し、人間が確認・修正してから確定します。

```
自然文（例: 夜 カレー）
    ↓
POST /drafts  →  event_drafts（status=draft）
    ↓
人間が確認
    ↓
POST /drafts/{id}/revise  （例: 「2杯だった」）
    ↓
POST /drafts/{id}/commit  →  quick_events（確定のみ）
```

- **raw_text**: 不変（event_drafts / quick_events 両方）
- **draft_json**: AI が生成・修正（mutable）
- **Discard**: レコードは残し `status=discarded` のみ

### 自然文入力例


| 入力          | 想定 event_type |
| ----------- | ------------- |
| `昼 カレー`     | meal          |
| `カレー作った 6食` | batch_created |
| `牛乳飲み切った`   | consumed      |


### POST /drafts（下書き作成）

```bash
curl -X POST http://127.0.0.1:8000/drafts \
  -H "Content-Type: application/json" \
  -d '{"text": "夜 カレー"}'
```

レスポンス例:

```json
{
  "id": 1,
  "raw_text": "夜 カレー",
  "event_type": "meal",
  "draft_json": {
    "meal_type": "dinner",
    "items": [{ "name": "カレー", "quantity": 1 }]
  },
  "confidence": 0.9,
  "status": "draft"
}
```

### POST /drafts/{draft_id}/revise（自然文修正）

```bash
curl -X POST http://127.0.0.1:8000/drafts/1/revise \
  -H "Content-Type: application/json" \
  -d '{"instruction": "2杯だった"}'
```

修正後の `draft_json` 例（数量変更）:

```json
{
  "meal_type": "dinner",
  "items": [{ "name": "カレー", "quantity": 2 }]
}
```

修正時は **event_type を維持** します（「使い切った」「作った」など種別変更の指示があるときだけ変更）。`meal` の下書きに `consumed` 用フィールドが混ざらないよう正規化します。

一部使用の例（`consumed` のとき。`target` に全部入れない）:

```json
{
  "target": "ナポリタンの素",
  "source": "業務スーパー",
  "quantity_total": 3,
  "quantity_used": 1,
  "quantity_remaining": 2
}
```

### POST /drafts/{draft_id}/commit（確定）

```bash
curl -X POST http://127.0.0.1:8000/drafts/1/commit
```

レスポンス:

```json
{
  "success": true,
  "draft_id": 1,
  "quick_event_id": 5,
  "status": "confirmed"
}
```

### POST /drafts/{draft_id}/discard（破棄）

```bash
curl -X POST http://127.0.0.1:8000/drafts/1/discard
```

### POST /drafts/{draft_id}/regenerate（raw_text から再生成）

```bash
curl -X POST http://127.0.0.1:8000/drafts/1/regenerate
```

### POST /quick-add（後方互換）

`POST /drafts` と同じ（下書きを作成。即 Commit しない）。

`event_type` の候補: `meal` / `batch_created` / `consumed` / `purchase` / `unknown`

## Ingredient Linking（購入食品 → Batch）

購入（OCR / レシート）と作り置き（Batch）を**緩く**紐付けます。在庫・重量管理は行いません。

```
Purchase（OCR 解析）
    ↓
parsed_events + purchase_items（商品行を DB に展開）
    ↓
Batch 下書き（例: カレー作った 6食）
    ↓
POST /drafts/{id}/ingredient-candidates  →  AI が候補名を提案（mutable）
    ↓
人間がチェック → POST /drafts/{id}/link-ingredients  →  draft_ingredient_links（DB が真実）
    ↓
POST /drafts/{id}/commit  →  quick_events + batch_ingredients
```

- **AI の責務**: `candidate_ingredients` の提案のみ（推測しすぎない）
- **DB の責務**: 人間が選んだ `purchase_item_id` のリンク（deterministic）
- **raw_text / captures**: 不変。AI 解釈（parsed_json / draft_json）は mutable。材料リンクは別テーブル

### purchase_items

`POST /analyze/{capture_id}` で `event_type=purchase` のとき、`parsed_json.items` から自動生成されます。  
機能追加前に解析済みのレシートは、**サーバー起動時・一覧表示時**に過去の `parsed_events` から自動バックフィルされます。


| カラム               | 説明                |
| ----------------- | ----------------- |
| `parsed_event_id` | 元の解析イベント          |
| `item_name`       | 商品名               |
| `price`           | 単価（任意）            |
| `quantity`        | 数量（今回は未使用・NULL 可） |


### POST /drafts/{draft_id}/ingredient-candidates

下書き内容と過去の `purchase_items` を Gemini に渡し、購入リスト内の名称だけを候補として返します。

```bash
curl -X POST http://127.0.0.1:8000/drafts/2/ingredient-candidates
```

レスポンス例:

```json
{
  "candidate_ingredients": ["鶏肉", "玉ねぎ", "カレールー"],
  "purchase_items": [
    { "id": 1, "item_name": "鶏肉", "price": 198, "parsed_event_id": 3 }
  ],
  "linked_purchase_item_ids": []
}
```

### POST /drafts/{draft_id}/link-ingredients

```bash
curl -X POST http://127.0.0.1:8000/drafts/2/link-ingredients \
  -H "Content-Type: application/json" \
  -d '{"purchase_item_ids": [1, 2, 3]}'
```

Commit 時（`event_type=batch_created`）に `batch_ingredients` へコピーされます。

一覧 UI の Batch 下書きカードからも「候補を取得」「紐付けを保存」が利用できます。

## AI 解析フロー（OCR / レシート）

```
iPhone OCR
    ↓
POST /capture  →  captures（raw_text をそのまま保存）
    ↓
POST /analyze/{capture_id}  →  Gemini API
    ↓
parsed_events（event_type, parsed_json, confidence）
    ↓
GET / 一覧に表示
```

1. ショートカットなどから `POST /capture` で文字列を保存
2. 一覧の「解析する」ボタン、または `POST /analyze/{capture_id}` で AI 解析
3. 結果は `parsed_events` に保存され、**captures.raw_text は変更されない**
4. モデル変更後は同じ capture を「再解析する」で新しい行を追加可能

## API

### GET /health

```json
{ "status": "ok" }
```

### POST /capture

```bash
curl -X POST http://127.0.0.1:8000/capture \
  -H "Content-Type: application/json" \
  -d '{"text": "OKストア 牛乳 298円", "source": "iphone"}'
```

レスポンス:

```json
{ "success": true }
```

### POST /analyze/{capture_id}

指定したキャプチャの `raw_text` を Gemini で解析し、`parsed_events` に保存します。

```bash
curl -X POST http://127.0.0.1:8000/analyze/1
```

レスポンス例:

```json
{
  "success": true,
  "event": {
    "event_type": "purchase",
    "parsed_json": {
      "store": "OKストア",
      "items": [
        { "name": "牛乳", "price": 298 }
      ]
    }
  }
}
```

`event_type` の候補（OCR 向け）: `purchase` / `inventory` / `food` / `unknown`

## iPhone ショートカットから POST する方法

1. **ショートカット** アプリで新規ショートカットを作成
2. 必要に応じて **テキストを認識**（OCR）や **クリップボードを取得** などで文字列を用意
3. **URL の内容を取得** アクションを追加
  - **URL**: `http://<MacのIPアドレス>:8000/capture`
  - **方法**: `POST`
  - **リクエスト本文**: `JSON`
  - **本文**（例）:

```json
{
  "text": "ショートカットの変数（認識したテキスト）",
  "source": "iphone"
}
```

1. 実行後、Mac のブラウザで [http://127.0.0.1:8000/](http://127.0.0.1:8000/) を開き、**解析する** で AI 解析

### 補足・トラブルシュート


| 症状               | 対処                                                     |
| ---------------- | ------------------------------------------------------ |
| 「サーバに接続できませんでした」 | ① `--host 0.0.0.0` で起動 ② URL が Mac の IP か ③ 同一 Wi‑Fi か |
| AI 解析が 503       | `.env` の `GEMINI_API_KEY` が正しいか確認                      |
| Mac の IP         | `ipconfig getifaddr en0` または システム設定 → ネットワーク           |


- iPhone の `localhost` は **iPhone 自身** を指します。Mac へ送るには `http://<MacのIP>:8000/capture` を使ってください。
- 本番運用前は HTTPS や認証の追加を検討してください（MVP では未実装）。

## 環境変数（.env）


| 変数               | 説明                  | デフォルト                        |
| ---------------- | ------------------- | ---------------------------- |
| `DATABASE_URL`   | SQLAlchemy 接続 URL   | `sqlite:///./data/lifeos.db` |
| `GEMINI_API_KEY` | Gemini API キー（解析必須） | なし                           |
| `GEMINI_MODEL`   | 使用モデル名              | `gemini-2.5-flash`           |


## ライセンス

個人利用・学習用の最小 MVP です。