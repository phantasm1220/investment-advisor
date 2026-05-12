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



# ── API コストトラッカー ──────────────────────────────────────────
class CostTracker:
    """プロセス内のAPI使用量とコストを追跡する"""
    _input_tokens:  int   = 0
    _output_tokens: int   = 0
    INPUT_RATE:     float = 0.10 / 1_000_000   # $0.10/1Mトークン
    OUTPUT_RATE:    float = 0.40 / 1_000_000   # $0.40/1Mトークン
    USD_JPY:        float = 160.0

    @classmethod
    def add(cls, input_tokens: int, output_tokens: int) -> None:
        cls._input_tokens  += input_tokens
        cls._output_tokens += output_tokens

    @classmethod
    def reset(cls) -> None:
        cls._input_tokens  = 0
        cls._output_tokens = 0

    @classmethod
    def get_cost_usd(cls) -> float:
        return (cls._input_tokens  * cls.INPUT_RATE +
                cls._output_tokens * cls.OUTPUT_RATE)

    @classmethod
    def get_cost_jpy(cls) -> float:
        return cls.get_cost_usd() * cls.USD_JPY

    @classmethod
    def get_summary(cls) -> str:
        """Discord末尾用のコストサマリー文字列を返す"""
        if cls._input_tokens == 0 and cls._output_tokens == 0:
            return ""
        jpy = cls.get_cost_jpy()
        usd = cls.get_cost_usd()
        return (
            f"💰 API: 入力{cls._input_tokens:,}tok / "
            f"出力{cls._output_tokens:,}tok / "
            f"¥{jpy:.3f} (${usd:.5f})"
        )


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
                try:
                    resp = self._session.post(
                        url, json=payload, headers=headers, timeout=self.timeout
                    )
                except (requests.exceptions.ReadTimeout,
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.ConnectionError) as te:
                    if attempt < self.max_retries:
                        wait = min(self.retry_delay * (2 ** (attempt - 1)), 8)
                        logger.warning(
                            f"[LLMClient] タイムアウト/接続エラー → "
                            f"{wait}秒待機してリトライ ({attempt}/{self.max_retries}): {te}"
                        )
                        time.sleep(wait)
                        continue
                    raise RuntimeError(
                        f"Gemini API が {self.max_retries} 回タイムアウトしました。"
                    ) from te
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
                data    = resp.json()
                content = data["choices"][0]["message"]["content"]

                # トークン使用量を記録
                usage = data.get("usage", {})
                in_tok  = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)
                if in_tok or out_tok:
                    CostTracker.add(in_tok, out_tok)

                return content

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
