"""
core/llm_client.py  v4.6.1

修正:
- _api_key property でキーが空の場合、os.environ の全キーをダンプしてデバッグ情報を出力
- セッションは接続再利用のみに使い、Authorizationは毎回リクエスト時に付与
"""
import os, json, re, time, logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self):
        self.base_url    = os.environ.get(
            "CCR_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        )
        self.model       = os.environ.get("LLM_MODEL",       "gemini-2.5-flash-lite")
        self.timeout     = int(os.environ.get("LLM_TIMEOUT",     "30"))
        self.max_retries = int(os.environ.get("LLM_MAX_RETRIES",  "3"))
        self.retry_delay = int(os.environ.get("LLM_RETRY_DELAY",  "2"))

        # セッションは接続再利用のみ（Authorizationはリクエスト毎に付与）
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        retry = Retry(total=2, backoff_factor=0.3,
                      status_forcelist=[500, 502, 503, 504])
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    @property
    def _api_key(self) -> str:
        """リクエスト毎に os.environ から取得する"""
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            # デバッグ: 環境変数の状況を出力
            env_keys = [k for k in os.environ if "GEMINI" in k or "API" in k]
            logger.error(
                "GEMINI_API_KEY が空です。\n"
                f"  .default ファイルの場所: {os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.default')}\n"
                f"  関連する環境変数: {env_keys}\n"
                "  対処: .default ファイルに GEMINI_API_KEY=AIzaSy... を記載してください"
            )
        return key

    def chat(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        url     = f"{self.base_url}/chat/completions"
        payload = {
            "model":      self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        }

        for attempt in range(1, self.max_retries + 1):
            api_key = self._api_key
            if not api_key:
                raise ValueError(
                    "GEMINI_API_KEY が設定されていません。\n"
                    ".default ファイルに以下を記載してください:\n"
                    "GEMINI_API_KEY=AIzaSy..."
                )

            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                resp = self._session.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
                # リトライ対象: 429(レート制限) / 503(高負荷) / 500,502,504(一時障害)
                RETRYABLE = {429, 500, 502, 503, 504}
                if resp.status_code in RETRYABLE:
                    wait = min(self.retry_delay * (2 ** (attempt - 1)), 8)
                    reason = {
                        429: "レート制限",
                        503: "API高負荷（一時的）",
                        500: "サーバーエラー",
                        502: "Bad Gateway",
                        504: "Gateway Timeout",
                    }.get(resp.status_code, "一時エラー")
                    logger.warning(
                        f"[LLMClient] {resp.status_code}({reason}) → "
                        f"{wait}秒待機してリトライ ({attempt}/{self.max_retries})"
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]

            except requests.HTTPError as e:
                logger.error(f"[LLMClient] HTTP {e.response.status_code}: {e.response.text[:200]}")
                raise
            except (KeyError, IndexError) as e:
                logger.error(f"[LLMClient] レスポンス解析エラー: {e}")
                raise

        raise RuntimeError(f"Gemini API が {self.max_retries} 回リトライしましたが失敗しました（429/503等）。\nhttps://ai.dev/rate-limit でプランを確認してください。")

    @staticmethod
    def safe_parse_json(text: str) -> dict:
        text = re.sub(r"```(?:json)?", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"JSONが見つかりません: {text[:200]}")
