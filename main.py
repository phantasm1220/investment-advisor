"""
main.py — エントリーポイント

【モード1】個別分析（株・ETF両対応）
  python main.py single --ticker 7203
  python main.py single --ticker 1545 --etf

【モード2】BUY銘柄ウォッチリスト確認（スキャン前に自動実行）
  python main.py watchlist           # 追跡中のBUY銘柄の損益をDiscordに送信

【モード3】個別株スキャン
  python main.py scan --test
  python main.py scan --top-n 100

【モード3】ETFスキャン
  python main.py etf --test
  python main.py etf --top-n 50

共通オプション:
  --dry-run : Discordに送信せずコンソール出力のみ

【設定の優先順位】
  1. 実行環境の環境変数（GitHub Actions Secrets など）← 最優先
  2. .default ファイル（ローカル実行時のAPIキー）
  3. .env ファイル（モデル名・エンドポイント等）
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_dir = os.path.dirname(os.path.abspath(__file__))


def _load_env_files() -> None:
    """
    設定ファイルを読み込む。

    優先順位（高い順）:
      1. 実行環境の環境変数（GitHub Actions の Secrets 等）← 既にセット済みなら上書きしない
      2. .default ファイル（ローカル用APIキー）← 環境変数が未セットの場合のみ適用
      3. .env ファイル（モデル名等の固定設定）← 最低優先

    GitHub Actions では Secrets が os.environ に既にセットされているため
    .default がなくても正常動作する。
    """
    from dotenv import load_dotenv

    # .env を先に読む（override=False で既存の環境変数は上書きしない）
    env_path = os.path.join(_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=False)

    # .default を読む（環境変数が未セットの項目だけ補完）
    default_path = os.path.join(_dir, ".default")
    if os.path.exists(default_path):
        parsed = _parse_env_file(default_path)
        for k, v in parsed.items():
            # 既に環境変数にセットされている場合は上書きしない
            if not os.environ.get(k):
                os.environ[k] = v
        logging.debug(f"[.default] {len(parsed)}件の設定を読み込みました")
    # .default がない場合（GitHub Actions等）は何もしない（Secretsが使われる）


def _parse_env_file(filepath: str) -> dict:
    result = {}
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key   = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[0] == value[-1]:
                    value = value[1:-1]
                if key:
                    result[key] = value
    except Exception as e:
        logging.warning(f".default の読み込み中にエラー: {e}")
    return result


# ── 設定ファイルを最初に読み込む ──────────────────────────
_load_env_files()

logger = logging.getLogger(__name__)

# ── 設定読み込み後にimport ──────────────────────────────
from config.settings import LOG_LEVEL, LOG_DIR, DISCORD_WEBHOOK_URL


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(LOG_DIR, "advisor.log"), encoding="utf-8"),
        ],
    )


def run_single(args):
    # ── ETFモード ──────────────────────────────────────────
    if args.etf:
        from core.etf_scanner import ETFScanner
        scanner = ETFScanner(discord_webhook_url=DISCORD_WEBHOOK_URL or None)
        result  = scanner.analyze_single(args.ticker, dry_run=args.dry_run)
        if result is None:
            sys.exit(1)
        from core.signal import Verdict
        sys.exit(
            0 if result.verdict.value in ("BUY", "STRONG_BUY")
            else 1 if result.verdict.value == "HOLD" else 2
        )

    # ── 個別株モード ────────────────────────────────────────
    from core.orchestrator import InvestmentOrchestrator
    from utils.market_data import MarketDataFetcher

    price, stock_name = args.price, ""
    fetcher = MarketDataFetcher()
    info    = fetcher.get_stock_info(args.ticker)

    if info is not None:
        stock_name = info.name
        if price is None:
            price = info.current_price
        # nan チェック
        if price != price or price is None or price <= 0:  # NaN check
            logging.warning(f"[{args.ticker}] yfinanceの株価が無効({price})。--price で手動指定してください。")
            if args.price is None:
                print(f"❌ {args.ticker} の株価取得に失敗しました（NaN）。--price オプションで手動指定してください。")
                sys.exit(1)
        chg = f"{'▲' if info.change_pct >= 0 else '▼'}{abs(info.change_pct):.2f}%"
        print(f"✅ {args.ticker} ({info.name})  現在値: ¥{price:,.0f}  {chg}")
    elif price is None:
        print(f"❌ 銘柄 {args.ticker} の株価取得に失敗。--price で手動指定してください。")
        sys.exit(1)

    decision = InvestmentOrchestrator(
        discord_webhook_url=DISCORD_WEBHOOK_URL or None
    ).run(ticker=args.ticker, current_price=price,
          dry_run=args.dry_run, stock_name=stock_name)

    from core.signal import Verdict
    sys.exit(
        0 if decision.verdict in (Verdict.BUY, Verdict.STRONG_BUY)
        else 1 if decision.verdict == Verdict.HOLD else 2
    )


def run_watchlist(args):
    """BUY銘柄ウォッチリストの損益チェックとDiscord送信"""
    from utils.buy_watchlist import BuyWatchlist
    wl = BuyWatchlist()
    if not wl:
        logger.info("[Watchlist] 監視中の銘柄はありません")
        print("📋 監視中のBUY銘柄はありません。scan を実行すると自動登録されます。")
        return
    print(f"📋 {len(wl)}銘柄のパフォーマンスをチェック中...")
    perfs = wl.check_performance()
    wl.send_discord_report(
        perfs=perfs,
        webhook_url=DISCORD_WEBHOOK_URL or "",
        dry_run=args.dry_run,
    )


def run_scan(args):
    # スキャン前にウォッチリストのパフォーマンスチェックを実行
    if not args.dry_run:
        try:
            from utils.buy_watchlist import BuyWatchlist
            wl = BuyWatchlist()
            if wl:
                logger.info("[Watchlist] スキャン前パフォーマンスチェック開始")
                perfs = wl.check_performance()
                if perfs:
                    wl.send_discord_report(
                        perfs=perfs,
                        webhook_url=DISCORD_WEBHOOK_URL or "",
                        dry_run=False,
                    )
        except Exception as e:
            logger.warning(f"[Watchlist] 事前チェック失敗（スキャンは続行）: {e}")

    from core.market_scanner import MarketScanner
    MarketScanner(discord_webhook_url=DISCORD_WEBHOOK_URL or None).run_scan(
        top_n=args.top_n, dry_run=args.dry_run, test_mode=args.test,
    )


def run_etf(args):
    from core.etf_scanner import ETFScanner
    ETFScanner(discord_webhook_url=DISCORD_WEBHOOK_URL or None).run_scan(
        top_n=args.top_n, dry_run=args.dry_run, test_mode=args.test,
    )


def main():
    setup_logging()
    parser = argparse.ArgumentParser(
        description="マルチエージェント投資助言システム",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("single", help="個別分析（株・ETF両対応）")
    p.add_argument("--ticker",  required=True)
    p.add_argument("--etf",     action="store_true")
    p.add_argument("--price",   type=float, default=None)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("scan", help="個別株：出来高上位N銘柄を一括分析")
    p.add_argument("--top-n",   type=int, default=100)
    p.add_argument("--test",    action="store_true")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("etf", help="ETF：出来高上位N件を一括分析")
    p.add_argument("--top-n",   type=int, default=50)
    p.add_argument("--test",    action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # watchlist サブコマンド
    p = sub.add_parser("watchlist", help="BUY銘柄の損益追跡レポートをDiscordに送信")
    p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    {"single": run_single, "scan": run_scan, "etf": run_etf,
     "watchlist": run_watchlist}[args.mode](args)


if __name__ == "__main__":
    main()
