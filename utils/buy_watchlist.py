"""
utils/buy_watchlist.py

BUY銘柄ウォッチリスト管理モジュール。

機能:
  - scan で BUY/STRONG_BUY 判定された銘柄を JSON ファイルに記録
  - 翌日以降の起動時に登録銘柄の現在株価を取得し、登録時からの損益を計算
  - 同一銘柄は重複登録しない（既登録なら監視を継続）
  - 登録から30日を超えた銘柄は自動的にアーカイブへ移動
  - Discord に実績レポートを送信

データ保存先: data/buy_watchlist.json
アーカイブ:   data/buy_watchlist_archive.json
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# ファイルパス
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHLIST_PATH  = os.path.join(_BASE_DIR, "data", "buy_watchlist.json")
ARCHIVE_PATH    = os.path.join(_BASE_DIR, "data", "buy_watchlist_archive.json")

TRACK_DAYS = 30   # 追跡期間（日）


@dataclass
class WatchEntry:
    """ウォッチリストの1エントリ"""
    ticker:       str
    name:         str
    registered_date: str      # 登録日 (YYYY-MM-DD)
    registered_price: float   # 登録時の株価
    verdict:      str         # BUY or STRONG_BUY
    confidence:   float       # 登録時の確信度
    sector:       str         # セクター


@dataclass
class DailyPerf:
    """1銘柄の日次パフォーマンス"""
    ticker:      str
    name:        str
    reg_date:    str
    reg_price:   float
    cur_price:   float
    days_held:   int
    pnl_pct:     float        # 損益率(%)
    pnl_yen:     float        # 損益額(円)
    is_expired:  bool         # 30日超過


class BuyWatchlist:
    """BUY銘柄ウォッチリストの管理クラス"""

    def __init__(self):
        os.makedirs(os.path.join(_BASE_DIR, "data"), exist_ok=True)
        self._entries: list[WatchEntry] = []
        self._load()

    # ──────────────────────────────────────────────
    #  登録・更新
    # ──────────────────────────────────────────────

    def register_buy_decisions(
        self,
        decisions,          # list[FinalDecision]
        stock_map: dict,    # {ticker: StockInfo}
    ) -> tuple[int, int]:
        """
        scan結果のBUY/STRONG_BUY銘柄をウォッチリストに登録する。
        既登録銘柄はスキップ。

        Returns:
            (new_count, skipped_count)
        """
        from core.signal import Verdict
        today = date.today().isoformat()
        existing_tickers = {e.ticker for e in self._entries}
        new_count = skipped = 0

        for dec in decisions:
            if dec.verdict not in (Verdict.BUY, Verdict.STRONG_BUY):
                continue
            if dec.ticker in existing_tickers:
                skipped += 1
                logger.debug(f"[Watchlist] {dec.ticker} は既登録のためスキップ")
                continue

            # 登録時の株価を取得
            price = None
            if dec.stock_overview and dec.stock_overview.current_price:
                p = dec.stock_overview.current_price
                if not math.isnan(p) and p > 0:
                    price = p
            if price is None:
                st = stock_map.get(dec.ticker)
                if st and st.current_price and not math.isnan(st.current_price):
                    price = st.current_price
            # まだ取れない場合はyfinanceから直接取得（登録時と同じ方法）
            if price is None:
                price = _fetch_price(dec.ticker)
            if price is None or math.isnan(price or float('nan')):
                logger.warning(f"[Watchlist] {dec.ticker} 株価取得失敗のためスキップ")
                continue

            name   = (dec.stock_overview.name if dec.stock_overview else dec.ticker)
            sector = (dec.agent_signals[0].raw_scores.get("sector", "不明")
                      if dec.agent_signals else "不明")

            entry = WatchEntry(
                ticker=dec.ticker,
                name=name,
                registered_date=today,
                registered_price=round(price, 1),
                verdict=dec.verdict.value,
                confidence=round(dec.composite_confidence, 3),
                sector=sector,
            )
            self._entries.append(entry)
            existing_tickers.add(dec.ticker)
            new_count += 1
            logger.info(f"[Watchlist] 新規登録: {dec.ticker}({name}) ¥{price:,.0f} {dec.verdict.value}")

        self._save()
        return new_count, skipped

    # ──────────────────────────────────────────────
    #  パフォーマンスチェック
    # ──────────────────────────────────────────────

    def check_performance(self) -> list[DailyPerf]:
        """
        全登録銘柄の現在株価を取得して損益を計算する。
        30日超過銘柄はアーカイブへ移動。
        """
        if not self._entries:
            return []

        today = date.today()
        results: list[DailyPerf] = []
        expired: list[WatchEntry] = []
        active:  list[WatchEntry] = []

        for entry in self._entries:
            reg_date  = date.fromisoformat(entry.registered_date)
            days_held = (today - reg_date).days
            is_expired = days_held > TRACK_DAYS

            cur_price = _fetch_price(entry.ticker)

            if cur_price is None:
                logger.warning(f"[Watchlist] {entry.ticker} 現在値取得失敗")
                cur_price = entry.registered_price   # 変化なし扱い

            pnl_pct = (cur_price - entry.registered_price) / entry.registered_price * 100
            pnl_yen = cur_price - entry.registered_price

            results.append(DailyPerf(
                ticker=entry.ticker,
                name=entry.name,
                reg_date=entry.registered_date,
                reg_price=entry.registered_price,
                cur_price=cur_price,
                days_held=days_held,
                pnl_pct=round(pnl_pct, 2),
                pnl_yen=round(pnl_yen, 1),
                is_expired=is_expired,
            ))

            if is_expired:
                expired.append(entry)
            else:
                active.append(entry)

        # 期限切れをアーカイブへ移動
        if expired:
            self._archive(expired)
            self._entries = active
            self._save()
            logger.info(f"[Watchlist] {len(expired)}件をアーカイブに移動")

        return results

    # ──────────────────────────────────────────────
    #  Discord 送信
    # ──────────────────────────────────────────────

    def send_discord_report(
        self,
        perfs:       list[DailyPerf],
        webhook_url: str,
        dry_run:     bool = False,
        new_count:   int = 0,
    ) -> bool:
        """パフォーマンスレポートをDiscordに送信する"""
        if not perfs:
            return True

        today_str = date.today().strftime("%Y/%m/%d")
        win   = sum(1 for p in perfs if p.pnl_pct > 0)
        lose  = sum(1 for p in perfs if p.pnl_pct < 0)
        flat  = len(perfs) - win - lose
        avg   = sum(p.pnl_pct for p in perfs) / len(perfs)

        # 銘柄行を生成（損益でソート）
        sorted_perfs = sorted(perfs, key=lambda p: p.pnl_pct, reverse=True)
        rows = ""
        for p in sorted_perfs:
            emoji = "📈" if p.pnl_pct > 1 else ("📉" if p.pnl_pct < -1 else "➡️")
            exp_note = " ⌛期限切れ" if p.is_expired else ""
            rows += (
                f"{emoji} `{p.ticker}` {p.name[:10]}  "
                f"登録:{p.reg_date}({p.days_held}日経過)  "
                f"登録値¥{p.reg_price:,.0f} → 現在¥{p.cur_price:,.0f}  "
                f"**{p.pnl_pct:+.2f}%** ({p.pnl_yen:+,.0f}円){exp_note}\n"
            )

        new_note = f"  本日新規追加: **{new_count}銘柄**" if new_count > 0 else ""

        embed = {
            "title": f"📊 BUY銘柄 追跡レポート — {today_str}",
            "description": (
                f"監視中: **{len(perfs)}銘柄**{new_note}\n"
                f"勝率: **{win}勝{lose}敗{flat}分** | 平均損益: **{avg:+.2f}%**"
            ),
            "color": 0x00C851 if avg > 0 else (0xFF4444 if avg < 0 else 0x95A5A6),
            "fields": [
                {
                    "name": "📈 BUY銘柄パフォーマンス一覧（登録時からの損益）",
                    "value": rows[:1024] or "データなし",
                    "inline": False,
                },
            ],
            "footer": {
                "text": f"⚠️ 投資助言ではありません。 | 追跡期間:{TRACK_DAYS}日間",
            },
            "timestamp": datetime.now().isoformat(),
        }

        if dry_run:
            logger.info(f"[Watchlist DryRun] {len(perfs)}銘柄 平均{avg:+.2f}%")
            for p in sorted_perfs[:5]:
                logger.info(f"  {p.ticker}: {p.pnl_pct:+.2f}% ({p.days_held}日)")
            return True

        if not webhook_url:
            logger.warning("[Watchlist] DISCORD_WEBHOOK_URL 未設定")
            return False

        try:
            resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
            if resp.status_code in (200, 204):
                logger.info(f"[Watchlist] Discord送信成功 ({len(perfs)}銘柄)")
                return True
            logger.error(f"[Watchlist] Discord送信失敗: {resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"[Watchlist] Discord通信エラー: {e}")
            return False

    # ──────────────────────────────────────────────
    #  永続化
    # ──────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(WATCHLIST_PATH):
            return
        try:
            with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._entries = [WatchEntry(**e) for e in raw.get("entries", [])]
            logger.debug(f"[Watchlist] {len(self._entries)}件ロード")
        except Exception as e:
            logger.warning(f"[Watchlist] ロードエラー: {e}")

    def _save(self) -> None:
        try:
            with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {"entries": [asdict(e) for e in self._entries],
                     "updated": datetime.now().isoformat()},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"[Watchlist] 保存エラー: {e}")

    def _archive(self, expired: list[WatchEntry]) -> None:
        """期限切れ銘柄をアーカイブファイルに追記する"""
        try:
            existing = []
            if os.path.exists(ARCHIVE_PATH):
                with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f).get("entries", [])
            existing.extend(asdict(e) for e in expired)
            with open(ARCHIVE_PATH, "w", encoding="utf-8") as f:
                json.dump({"entries": existing,
                           "updated": datetime.now().isoformat()},
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Watchlist] アーカイブエラー: {e}")

    def __len__(self): return len(self._entries)
    def __bool__(self): return bool(self._entries)


def _fetch_price(ticker: str) -> Optional[float]:
    """
    yfinanceで最新の終値を取得する。
    period="5d" で取得し、最新の有効な終値を返す。
    登録時と同じ取得方法に統一することで価格乖離を防ぐ。
    """
    sym = ticker if ticker.endswith(".T") else f"{ticker}.T"
    try:
        hist = yf.Ticker(sym).history(period="5d")
        if hist.empty:
            return None
        close = hist["Close"].dropna()
        if close.empty:
            return None
        p = float(close.iloc[-1])
        return None if (math.isnan(p) or p <= 0) else p
    except Exception:
        return None
