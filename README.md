# LifeOS — 生活イベント収集 MVP

家計簿ではなく、iPhone から送られた OCR 文字列を**そのまま**保存し、Gemini API で**構造化イベント**に変換する最小基盤です。

- **captures**: 生の OCR 文字列（不変の生データ）
- **parsed_events**: AI による解釈結果（再解析可能）

## ディレクトリ構成

```
lifeos/
├── app/
│   ├── main.py              # FastAPI 起動・一覧・ルート登録
│   ├── db.py                # SQLite 接続
│   ├── models.py            # captures / parsed_events
│   ├── schemas.py           # API の型定義
│   ├── routes/
│   │   ├── capture.py       # POST /capture
│   │   └── analyze.py       # POST /analyze/{capture_id}
│   ├── services/
│   │   └── ai_parser.py     # Gemini 解析
│   └── templates/
│       └── index.html       # 一覧 HTML（解析ボタン付き）
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

コスト重視の場合、デフォルトで `gemini-2.5-flash` を使用します（失敗時は `gemini-1.5-flash` にフォールバック）。  
モデルを変える場合:

```env
GEMINI_MODEL=gemini-1.5-flash
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

## AI 解析フロー

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

`event_type` の候補: `purchase` / `inventory` / `food` / `unknown`

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