# AIツール — Kaizen AI

コンサル業務で使う自社製AIツール。**「自らAI経営を実践」の実証**であり、商談デモにもそのまま使えます。

| ツール | 何をするか |
|---|---|
| `gijiroku.py` | 会議の文字起こし → 構造化議事録（決定事項・TODO表・論点・保留事項） |
| `teiansho.py` | 顧客ヒアリングメモ → 社内テンプレート準拠の提案書ドラフト（料金はサービスカタログ参照） |

## セットアップ（初回のみ）

```bash
cd tools
pip install -r requirements.txt

# APIキーを環境変数に設定（https://platform.claude.com で取得）
export ANTHROPIC_API_KEY=sk-ant-...
```

> ⚠️ **APIキーは絶対にコミットしない。** `.gitignore` で `.env` 等は除外済みですが、
> コードやメモへの直書きも禁止。環境変数または `.env`（ローカルのみ）で管理すること。

## 使い方

### 議事録要約

```bash
python gijiroku.py 文字起こし.txt
python gijiroku.py 文字起こし.txt -o 議事録.md --title "◯◯社 定例会議" --attendees "田中, 佐藤"
```

- 出力: `<入力名>_議事録.md`（`-o` で変更可）
- 文字起こしにない情報は推測せず「（要確認）」と明記する設計

### 提案書ドラフト生成

```bash
python teiansho.py ヒアリングメモ.txt --client "サンプル商事"
```

- テンプレート: [`../templates/proposal-template.md`](../templates/proposal-template.md) に自動準拠
- 料金: [`../docs/services/service-catalog.md`](../docs/services/service-catalog.md) のレンジ内で提案
- 不明点は「（要確認: 〜）」として残す設計 — **推測で埋めない**

## 運用ルール

1. **生成物は必ず人が確認してから使う** — 特に提案書の金額・スコープはオーナー承認必須（ガバナンスのレッドライン）
2. **顧客の機密情報を入力する場合** — 顧客との合意（生成AI利用可否・学習利用オフ）を事前に確認（`templates/engagement-checklist.md` 参照）
3. 生成コスト目安: 1回あたり数十円〜数百円（入力量に依存。実行後にトークン数が表示される）

## 技術メモ

- モデル: `claude-opus-5`（Anthropic）、ストリーミング出力
- 実行には Python 3.10+ が必要
