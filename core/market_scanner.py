"""
core/market_scanner.py
市場スキャナー。待機なし・銘柄名をorchestratorに渡す。
"""
import logging
from typing import Optional
from datetime import datetime
from collections import defaultdict

from core.llm_client import LLMClient
from core.orchestrator import InvestmentOrchestrator
from core.market_signal import MarketScanResult, SectorSummary
from core.signal import FinalDecision, Verdict
from utils.market_data import MarketDataFetcher, StockInfo
from utils.discord_notifier import DiscordNotifier

logger = logging.getLogger(__name__)


def _scan_cost_summary() -> str:
    """スキャン総評用コストサマリー"""
    try:
        from core.llm_client import CostTracker
        return CostTracker.get_summary()
    except Exception:
        return ""



def _fmt_candidates_chunked(codes: list, decisions, chunk_size: int = 1020) -> list[str]:
    """候補銘柄を1024文字以内のチャンクに分割して返す"""
    full_text = _fmt_candidates(codes, decisions) or "なし"
    if len(full_text) <= chunk_size:
        return [full_text]
    # 行単位で分割
    lines = full_text.split("\n")
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > chunk_size:
            if current:
                chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())
    return chunks or ["なし"]


VERDICT_SHORT = {
    "STRONG_BUY":  "🚀強買",
    "BUY":         "✅買い",
    "HOLD":        "⏸様子見",
    "SELL":        "⚠️売り",
    "STRONG_SELL": "🔴強売",
}

def _fmt_candidates(codes: list, decisions) -> str:
    """候補銘柄を「コード 企業名 株価 騰落 判断 確信度 出来高順位 特殊シグナル」形式で整形"""
    dmap = {d.ticker: d for d in decisions}
    lines = []
    for c in codes:
        d = dmap.get(c)
        if not d:
            lines.append(f"`{c}`")
            continue

        # 企業名（日本語優先・最大12文字）
        from utils.name_resolver import get_jp_name as _gjn
        raw_name = (d.stock_overview.name if d.stock_overview else c)
        name = _gjn(c, None) or raw_name
        name = name[:12]

        # 株価・騰落
        if d.stock_overview and d.stock_overview.current_price:
            price = f"¥{d.stock_overview.current_price:,.0f}"
            chg   = f"{'▲' if d.stock_overview.change_pct >= 0 else '▼'}{abs(d.stock_overview.change_pct):.1f}%"
        else:
            price, chg = "N/A", ""

        verdict = VERDICT_SHORT.get(d.verdict.value, d.verdict.value)
        conf    = f"{d.composite_confidence:.0%}"

        # 出来高順位
        rank = 0
        for sig in (d.agent_signals or []):
            r = sig.raw_scores.get("volume_rank", 0)
            if r: rank = r; break
        rank_str = f"出来高{rank}位" if rank > 0 else ""

        # 特殊シグナル
        signals = []
        for sig in (d.agent_signals or []):
            if sig.raw_scores.get("breakout_52w_flag"):
                signals.append("🚀52週高値")
                break
        for sig in (d.agent_signals or []):
            if sig.raw_scores.get("volume_surge_flag"):
                signals.append("🔥出来高急増")
                break

        extra = "  ".join(filter(None, [rank_str] + signals))
        line  = f"`{c}` {name}  {price} {chg}  {verdict} {conf}"
        if extra:
            line += f"  {extra}"
        lines.append(line)
    return "\n".join(lines)


class MarketScanner:
    def __init__(self, discord_webhook_url: Optional[str] = None):
        self._fetcher      = MarketDataFetcher()
        self._orchestrator = InvestmentOrchestrator(discord_webhook_url=discord_webhook_url)
        self._notifier     = DiscordNotifier(webhook_url=discord_webhook_url)
        self._llm          = LLMClient()

    def run_scan(
        self,
        top_n: int = 100,
        dry_run: bool = False,
        test_mode: bool = False,
    ) -> MarketScanResult:
        effective_n = 5 if test_mode else top_n
        logger.info(f"=== 市場スキャン開始 (上位{effective_n}銘柄) ===")

        stocks = self._fetcher.get_top_volume_stocks(top_n=effective_n)
        if not stocks:
            raise RuntimeError("市場データの取得に失敗しました")

        logger.info(f"分析対象: {len(stocks)}銘柄")
        for i, s in enumerate(stocks, 1):
            logger.info(
                f"  [{i:3d}] {s.code} {s.name[:15]:<15} "
                f"¥{s.current_price:>8,.0f} 出来高:{s.volume_today:>10,} "
                f"({s.volume_ratio:.1f}x) {s.change_pct:+.1f}%"
            )

        # ④ 出来高急増シグナル（前日比2倍以上 + 株価上昇）
        volume_surge = {
            s.code for s in stocks
            if s.volume_ratio >= 2.0 and s.change_pct >= 1.0
        }
        # ⑤ 52週高値ブレイクアウト（yfinanceで確認済みの銘柄）
        breakout_52w = set()
        try:
            from utils.market_data import MarketDataFetcher as _MDF
            import yfinance as _yf
            for s in stocks[:50]:   # 上位50件のみ（速度優先）
                try:
                    hist = _yf.Ticker(
                        s.code if s.code.endswith(".T") else f"{s.code}.T"
                    ).history(period="252d")
                    if not hist.empty:
                        close = hist["Close"].dropna()
                        if len(close) >= 50:
                            h52 = float(close.iloc[:-1].max())
                            cur = float(close.iloc[-1])
                            if cur >= h52 * 0.99:   # 52週高値の99%以上 = ブレイク/接近
                                breakout_52w.add(s.code)
                except Exception:
                    pass
        except Exception:
            pass
        if volume_surge:
            logger.info(f"[出来高急増] {len(volume_surge)}件: {list(volume_surge)[:5]}")
        if breakout_52w:
            logger.info(f"[52週ブレイク] {len(breakout_52w)}件: {list(breakout_52w)[:5]}")

        # 銘柄コード → 出来高順位のマップ（1始まり）
        rank_map = {s.code: i for i, s in enumerate(stocks, 1)}

        decisions: list[FinalDecision] = []
        for i, stock in enumerate(stocks, 1):
            logger.info(
                f"[{i}/{len(stocks)}] {stock.code} {stock.name} "
                f"¥{stock.current_price:,.0f} 分析中..."
            )
            try:
                decision = self._orchestrator.run(
                    ticker=stock.code,
                    current_price=stock.current_price,
                    dry_run=True,  # スキャン中は個別通知しない（総評のみ）
                    stock_name=stock.name,  # ★ 銘柄名を渡す
                )
                # 特殊シグナルを raw_scores に付与
                if decision.agent_signals:
                    for sig in decision.agent_signals:
                        sig.raw_scores["volume_surge_flag"]   = stock.code in volume_surge
                        sig.raw_scores["breakout_52w_flag"]   = stock.code in breakout_52w
                        sig.raw_scores["volume_rank"]         = rank_map.get(stock.code, 0)
                decisions.append(decision)
            except Exception as e:
                logger.error(f"  [{stock.code}] 分析エラー（スキップ）: {e}")

        if not decisions:
            raise RuntimeError("全銘柄の分析に失敗しました")

        result = self._build_scan_result(stocks, decisions, rank_map=rank_map)
        logger.info("[scan] _build_scan_result 完了")
        self._send_market_overview(result, dry_run=dry_run)
        logger.info("[scan] _send_market_overview 完了")

        # BUY銘柄をウォッチリストに登録
        logger.info(f"[scan] ウォッチリスト登録開始 dry_run={dry_run}")
        if not dry_run:
            try:
                logger.info("[Watchlist] BuyWatchlist インスタンス生成中...")
                from utils.buy_watchlist import BuyWatchlist
                wl = BuyWatchlist()
                logger.info(f"[Watchlist] 既存件数:{len(wl)}件 登録処理開始...")
                stock_map_for_wl = {s.code: s for s in stocks}
                new_cnt, skip_cnt = wl.register_buy_decisions(decisions, stock_map_for_wl)
                logger.info(f"[Watchlist] 新規登録:{new_cnt}件 スキップ:{skip_cnt}件 合計:{len(wl)}件")
                # 株価未取得（¥0）の手動登録銘柄を補完
                filled = wl.fill_missing_prices()
                if filled:
                    logger.info(f"[Watchlist] 手動登録銘柄の株価補完: {filled}件")
            except BaseException as e:
                logger.warning(f"[Watchlist] 登録失敗: {type(e).__name__}: {e}", exc_info=True)

        logger.info(f"=== 市場スキャン完了: {len(decisions)}銘柄分析 ===")
        return result

    # ──────────────────────────────────────────────

    def _build_scan_result(
        self,
        stocks: list[StockInfo],
        decisions: list[FinalDecision],
        rank_map: dict | None = None,
    ) -> MarketScanResult:
        decisions.sort(key=lambda d: d.composite_confidence, reverse=True)
        stock_map = {s.code: s for s in stocks}

        sector_data: dict[str, list] = defaultdict(list)
        for dec in decisions:
            st = stock_map.get(dec.ticker)
            sector = st.sector if st else "不明"
            sector_data[sector].append((dec, st))

        sector_summaries: list[SectorSummary] = []
        for sector, items in sector_data.items():
            decs = [i[0] for i in items]
            sts  = [i[1] for i in items if i[1]]
            bull = sum(1 for d in decs if d.verdict in (Verdict.BUY, Verdict.STRONG_BUY))
            bear = sum(1 for d in decs if d.verdict in (Verdict.SELL, Verdict.STRONG_SELL))
            avg_c = sum(d.composite_confidence for d in decs) / len(decs)
            mom  = sum(s.volume_ratio for s in sts) / len(sts) if sts else 1.0
            best = max(decs, key=lambda d: d.composite_confidence)
            bst_s = stock_map.get(best.ticker)
            sector_summaries.append(SectorSummary(
                sector=sector,
                avg_confidence=avg_c,
                bullish_count=bull,
                bearish_count=bear,
                top_ticker=best.ticker,
                top_ticker_name=bst_s.name if bst_s else best.ticker,
                momentum_score=mom,
            ))

        sector_summaries.sort(key=lambda s: s.momentum_score, reverse=True)
        hot_sectors = [s.sector for s in sector_summaries[:6]]

        # 出来高急増・52週ブレイクフラグをdecisionから取得
        def _get_flag(d, key):
            for sig in (d.agent_signals or []):
                if sig.raw_scores.get(key):
                    return True
            return False

        def _get_rank(d):
            for sig in (d.agent_signals or []):
                r = sig.raw_scores.get("volume_rank", 0)
                if r: return r
            return rank_map.get(d.ticker, 9999) if rank_map else 9999

        # 上昇候補: 52週ブレイク > 出来高急増 > 確信度 の優先順でソート
        rising_decisions = [
            d for d in decisions
            if d.verdict in (Verdict.STRONG_BUY, Verdict.BUY)
        ]
        rising_decisions.sort(
            key=lambda d: (
                -int(_get_flag(d, "breakout_52w_flag")),   # 52週ブレイクを最優先
                -int(_get_flag(d, "volume_surge_flag")),   # 次に出来高急増
                -d.composite_confidence,                   # 次に確信度
            )
        )
        rising = [d.ticker for d in rising_decisions]
        # 急落リスク: SELL以上 OR (HOLD かつ確信度高 かつ前日大幅下落)
        # 確信度閾値を0.65→0.50に緩和し、急落兆候銘柄も含める
        falling = [
            d.ticker for d in sorted(decisions,
                key=lambda d: d.composite_confidence, reverse=True)
            if d.verdict in (Verdict.STRONG_SELL, Verdict.SELL)
            and d.composite_confidence >= 0.50
        ]
        # SELL判定が少ない場合はHOLDで下落モメンタムが強い銘柄を補完
        if len(falling) < 3:
            falling_extra = [
                d.ticker for d in decisions
                if d.ticker not in falling
                and d.verdict == Verdict.HOLD
                and d.stock_overview is not None
                and d.stock_overview.change_pct <= -2.0
                and d.composite_confidence >= 0.60
            ]
            falling = (falling + falling_extra)[:15]

        overview = self._generate_market_overview(
            decisions, sector_summaries, hot_sectors, rising, falling, stock_map
        )

        return MarketScanResult(
            scan_date=datetime.now(),
            total_stocks_analyzed=len(decisions),
            decisions=decisions,
            market_overview=overview,
            hot_sectors=hot_sectors,
            rising_candidates=rising,
            falling_candidates=falling,
            sector_summaries=sector_summaries,
        )

    def _generate_market_overview(self, decisions, sectors, hot_sectors,
                                   rising, falling, stock_map) -> str:
        top5_text = ""
        for d in decisions[:5]:
            st = stock_map.get(d.ticker)
            name = st.name[:12] if st else d.ticker
            price = f"¥{st.current_price:,.0f}" if st else ""
            vol   = f"{st.volume_ratio:.1f}x" if st else ""
            top5_text += (
                f"  {d.ticker}({name}): {d.verdict.value} "
                f"確信度{d.composite_confidence:.0%} {price} 出来高{vol}\n"
            )

        sector_text = "\n".join(
            f"  {s.sector}: 強気{s.bullish_count}/弱気{s.bearish_count} "
            f"モメンタム{s.momentum_score:.2f}x"
            for s in sectors[:10]
        )

        from utils.market_theme_fetcher import get_themes_text as get_theme_context_for_prompt
        theme_ctx = get_theme_context_for_prompt()

        # 上昇候補・急落リスクに企業名・株価を付与
        def fmt_with_name(codes, smap, n=5):
            items = []
            for c in codes[:n]:
                st = smap.get(c)
                name  = st.name[:10] if st else c
                price = f"¥{st.current_price:,.0f}" if st else ""
                items.append(f"{c}({name}){price}")
            return ", ".join(items) if items else "なし"

        rising_text  = fmt_with_name(rising,  stock_map)
        falling_text = fmt_with_name(falling, stock_map)

        prompt = f"""
本日の東証出来高上位{len(decisions)}銘柄の分析結果を総括してください。

{theme_ctx}

【出来高上位銘柄TOP5の判断（コード・企業名・現在値・判断）】
{top5_text}
【業種別集計（モメンタム上位）】
{sector_text}
【上昇候補銘柄（コード・企業名・現在値）】: {rising_text}
【急落リスク銘柄（コード・企業名・現在値）】: {falling_text}
【注目セクター】: {', '.join(hot_sectors)}

以下3点を各200字以内で日本語でまとめてください：
1. 現在の市場テーマ（AI・半導体・エネルギー・防衛等）と資金フロー
2. 短期・中期で上昇が期待されるセクター・銘柄と根拠（企業名を明示）
3. 急落リスクがある銘柄・セクターと注意理由（企業名を明示）
最後に「総合判断: 強気/中立/弱気」を一言で。
重要: AI・半導体・エネルギーへの言及を必ず含めること。
"""
        try:
            return self._llm.chat(
                "あなたは日本株市場のアナリストです。データに基づく簡潔な相場見通しを提供してください。",
                prompt, max_tokens=600
            )
        except Exception as e:
            logger.error(f"市場総評の生成に失敗: {e}")
            return "市場総評の生成に失敗しました。"

    def _send_market_overview(self, result: MarketScanResult, dry_run: bool) -> None:
        timestamp = result.scan_date.strftime("%Y-%m-%d %H:%M JST")

        sector_lines = ""
        for s in result.sector_summaries[:8]:
            bar = "🔴" if s.bullish_count < s.bearish_count else "🟢"
            sector_lines += (
                f"{bar} **{s.sector}** — "
                f"強気:{s.bullish_count} 弱気:{s.bearish_count} "
                f"モメンタム:{s.momentum_score:.1f}x\n"
            )

        # フィールドを動的に構築（上昇候補・急落リスクは全件・複数フィールドに分割）
        fields = []

        # 注目セクター
        fields.append({
            "name": "🔥 注目セクター（モメンタム上位）",
            "value": "  ".join(f"**{s}**" for s in result.hot_sectors) or "なし",
            "inline": False,
        })

        # 上昇候補：全件を1024文字チャンクに分割して複数フィールド
        rising_chunks = _fmt_candidates_chunked(result.rising_candidates, result.decisions)
        for i, chunk in enumerate(rising_chunks):
            title = (f"📈 上昇候補銘柄（全{len(result.rising_candidates)}件）"
                     if i == 0 else f"📈 上昇候補（続き {i+1}）")
            fields.append({"name": title, "value": chunk, "inline": False})

        # 急落リスク：全件を1024文字チャンクに分割
        falling_chunks = _fmt_candidates_chunked(result.falling_candidates, result.decisions)
        for i, chunk in enumerate(falling_chunks):
            title = (f"📉 急落リスク銘柄（全{len(result.falling_candidates)}件）"
                     if i == 0 else f"📉 急落リスク（続き {i+1}）")
            fields.append({"name": title, "value": chunk, "inline": False})

        # 業種別モメンタム
        fields.append({
            "name": "🏭 業種別モメンタム（上位5）",
            "value": sector_lines or "データなし",
            "inline": False,
        })

        # 分析統計
        fields.append({
            "name": "📋 分析統計",
            "value": (
                f"分析銘柄数: **{result.total_stocks_analyzed}銘柄**\n"
                f"強気: **{sum(1 for d in result.decisions if d.verdict in (Verdict.BUY, Verdict.STRONG_BUY))}銘柄**  "
                f"弱気: **{sum(1 for d in result.decisions if d.verdict in (Verdict.SELL, Verdict.STRONG_SELL))}銘柄**  "
                f"中立: **{sum(1 for d in result.decisions if d.verdict == Verdict.HOLD)}銘柄**"
            ),
            "inline": False,
        })

        embed = {
            "title": f"📊 市場スキャン総評 — {result.scan_date.strftime('%Y/%m/%d')}",
            "description": result.market_overview[:1800],
            "color": 0x1E90FF,
            "fields": fields,
            "footer": {"text": (
                f"⚠️ 本レポートは投資助言ではありません。 | {timestamp}  "
                + _scan_cost_summary()
            )},
            "timestamp": result.scan_date.isoformat(),
        }

        payload = {"embeds": [embed]}

        if dry_run:
            logger.info(f"[Discord Dry Run] 市場総評:\n{result.market_overview[:500]}")
            logger.info(f"  注目セクター: {result.hot_sectors}")
            logger.info(f"  上昇候補: {result.rising_candidates[:5]}")
            return

        if not self._notifier.webhook_url:
            logger.warning("DISCORD_WEBHOOK_URL 未設定のため通知をスキップ")
            return

        import requests as req
        try:
            resp = req.post(self._notifier.webhook_url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                logger.info("[Discord] 市場総評の送信成功")
            else:
                logger.error(f"[Discord] 送信失敗: {resp.status_code}")
        except Exception as e:
            logger.error(f"[Discord] 通信エラー: {e}")
