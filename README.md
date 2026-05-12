# 🤖 Multi-Agent Investment Advisory System

マルチエージェント型投資助言システム。ファンダメンタルズ・マクロ・テクニカルの3つの専門エージェントが独立して分析を行い、マネージャーエージェントがベイズ推定で統合・判断してDiscordに通知します。

## アーキテクチャ

```
investment_advisor/
├── agents/
│   ├── fundamentals_agent.py   # ファンダメンタルズ担当
│   ├── macro_agent.py          # マクロ担当
│   ├── technical_agent.py      # テクニカル担当
│   └── manager_agent.py        # マネージャー（統合・判断）
├── core/
│   ├── base_agent.py           # 基底エージェントクラス
│   ├── orchestrator.py         # エージェント間の調整
│   ├── signal.py               # 共通シグナルデータクラス
│   └── conflict_resolver.py    # 矛盾解消ロジック
├── utils/
│   ├── discord_notifier.py     # Discord通知
│   ├── data_fetcher.py         # データ取得ユーティリティ
│   └── kelly_criterion.py      # ケリー基準によるポジションサイズ
├── config/
│   └── settings.py             # 設定・環境変数
├── tests/
│   └── test_agents.py
├── main.py                     # エントリーポイント
└── requirements.txt
```

## セットアップ

```bash
pip install -r requirements.txt
cp config/settings.py.example config/settings.py  # .envに認証情報を設定
python main.py --ticker 7203  # トヨタを分析例
```

## 環境変数 (.env)

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
ANTHROPIC_API_KEY=sk-ant-...
```
