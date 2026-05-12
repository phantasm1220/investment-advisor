"""
config/settings.py
環境変数と設定値の管理
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── API認証 ───
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ─── エージェント設定 ───
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.70"))
KELLY_FRACTION       = float(os.environ.get("KELLY_FRACTION", "0.5"))
MAX_POSITION_SIZE    = float(os.environ.get("MAX_POSITION_SIZE", "25.0"))  # %

# ─── ログ設定 ───
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_DIR   = os.environ.get("LOG_DIR", "logs")
