# ============================================================
#  Dockerfile — 投資助言システム
#  対応プラットフォーム: Cloud Run / Railway / Fly.io / Render
# ============================================================

FROM python:3.12-slim

# TA-Lib のシステム依存ライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        make \
        wget \
    && wget -q https://sourceforge.net/projects/ta-lib/files/ta-lib/0.4.0/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib && ./configure --prefix=/usr && make && make install \
    && cd .. && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存関係を先にコピー（キャッシュ活用）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir ta-lib

# アプリケーションコードをコピー
COPY . .

# ログディレクトリを作成
RUN mkdir -p logs

# 環境変数のデフォルト（実運用は Cloud Run の Secret Manager や Railway Variables で上書き）
ENV LOG_LEVEL=INFO \
    LOG_DIR=logs \
    LLM_MODEL=gemini-2.0-flash \
    CCR_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai \
    CONFIDENCE_THRESHOLD=0.70 \
    KELLY_FRACTION=0.5

# ヘルスチェック用エンドポイント（scheduler モードでは不要なら削除可）
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import core.llm_client" || exit 1

# デフォルトはコマンドなし（Cloud Run Jobs や Railway Cron で引数を渡す）
ENTRYPOINT ["python", "main.py"]
CMD ["--ticker", "7203", "--price", "3250", "--dry-run"]
