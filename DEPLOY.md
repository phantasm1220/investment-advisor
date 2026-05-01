# ☁️ クラウドデプロイ ガイド

## 1. 事前準備（共通）

`.env.example` をコピーして `.env` を作成し、以下を設定してください。

```bash
cp .env.example .env
```

`.env` に記入する値：

| キー | 値の取得場所 |
|------|------------|
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey |
| `DISCORD_WEBHOOK_URL` | Discord → サーバー設定 → 連携サービス → Webhooks |

> ⚠️ `.env` は絶対に Git にコミットしないこと（`.gitignore` に追加済み）

---

## 2. クラウド実行オプション

### ✅ Option A: GitHub Actions（完全無料・最も簡単）

毎朝9時(JST)に自動実行。クレジットカード不要。

```
1. GitHub にリポジトリを作成して push
2. Settings → Secrets and variables → Actions で以下を登録:
   - GEMINI_API_KEY
   - DISCORD_WEBHOOK_URL
3. Actions タブ → "Investment Advisor (Daily)" → 手動実行でテスト
4. 毎朝月〜金の9時に自動実行
```

### ✅ Option B: Railway（シンプル・無料枠あり）

```bash
# Railway CLI をインストール
npm install -g @railway/cli

# ログインしてデプロイ
railway login
railway init
railway up

# 環境変数を設定（Webダッシュボードでも可）
railway variables set GEMINI_API_KEY=your_key
railway variables set DISCORD_WEBHOOK_URL=your_webhook
```

`railway.toml` に Cron スケジュール（毎朝9時）が設定済みです。

### ✅ Option C: Google Cloud Run Jobs（スケーラブル）

```bash
# gcloud CLI セットアップ後
PROJECT_ID=your-project-id
REGION=asia-northeast1

# Docker イメージをビルド & push
gcloud builds submit --tag gcr.io/$PROJECT_ID/investment-advisor

# Cloud Run Job を作成
gcloud run jobs create investment-advisor \
  --image gcr.io/$PROJECT_ID/investment-advisor \
  --region $REGION \
  --set-env-vars LLM_MODEL=gemini-2.0-flash \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest \
  --set-secrets DISCORD_WEBHOOK_URL=discord-webhook:latest \
  --args="--ticker,7203,--price,3250"

# Cloud Scheduler で毎朝9時に自動実行
gcloud scheduler jobs create http advisor-daily \
  --schedule "0 0 * * 1-5" \
  --uri "https://REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/PROJECT_ID/jobs/investment-advisor:run" \
  --oauth-service-account-email YOUR_SA@PROJECT_ID.iam.gserviceaccount.com
```

---

## 3. ローカルでの動作確認

```bash
# 依存関係インストール
pip install -r requirements.txt

# .env を読み込んでテスト実行（Discordには送信しない）
python main.py --ticker 7203 --price 3250 --dry-run

# 本番実行（Discordに通知）
python main.py --ticker 7203 --price 3250
```

---

## 4. 構成図

```
[GitHub Actions / Railway / Cloud Run]
         │  毎朝9時(JST) 自動起動
         ▼
    main.py --ticker 7203 --price 3250
         │
         ▼
  InvestmentOrchestrator
    ├── FundamentalsAgent ─┐
    ├── MacroAgent         ├─ 並列実行 → Gemini API (claude-code-router経由)
    └── TechnicalAgent    ─┘
         │
         ▼
    ManagerAgent（統合・矛盾解消・ケリー基準）
         │
         ▼
    Discord Webhook 通知
```
