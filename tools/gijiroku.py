#!/usr/bin/env python3
"""議事録要約ツール — Kaizen AI

会議の文字起こしテキストから、構造化された議事録（Markdown）を生成する。

使い方:
    python gijiroku.py 文字起こし.txt
    python gijiroku.py 文字起こし.txt -o 議事録.md --title "定例会議" --attendees "田中, 佐藤"

事前準備:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...   # キーはコードに書かない
"""

import argparse
import sys
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
あなたは業務効率化コンサルティング会社 Kaizen AI の議事録作成の専門家です。
会議の文字起こしから、実務でそのまま使える議事録をMarkdownで作成してください。

# 出力構成（この順・この見出しで）
1. `# 議事録 — <会議名>`（会議名が不明なら内容から適切に命名）
2. `## 会議概要` — 日時・出席者・目的（文字起こしから分かる範囲のみ）
3. `## サマリー` — 3〜5行で会議全体の要約
4. `## 決定事項` — 箇条書き。決定に至った理由も1行で添える
5. `## TODO・アクションアイテム` — 表形式（| 項目 | 担当 | 期限 |）。担当・期限が不明な欄は「（要確認）」
6. `## 議論の要点` — 主要な論点ごとに小見出し＋要約
7. `## 保留・未決事項` — 次回に持ち越された論点

# ルール
- 文字起こしに書かれていないことを推測で補わない。不明な点は「（要確認）」と明記する
- 数字・日付・固有名詞は文字起こしの表記を正確に保つ
- 発言の逐語再現ではなく、要点を整理して書く
- 決定事項とTODOの漏れは実務上の事故につながるため、最優先で網羅する
"""


def build_user_prompt(transcript: str, title: str | None, attendees: str | None) -> str:
    meta = []
    if title:
        meta.append(f"会議名: {title}")
    if attendees:
        meta.append(f"出席者: {attendees}")
    meta_block = ("\n".join(meta) + "\n\n") if meta else ""
    return (
        f"{meta_block}以下の会議文字起こしから議事録を作成してください。\n\n"
        f"<transcript>\n{transcript}\n</transcript>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="会議の文字起こしから議事録を生成する")
    parser.add_argument("input", help="文字起こしテキストファイル（.txt など）")
    parser.add_argument("-o", "--output", help="出力先Markdownファイル（省略時: <入力名>_議事録.md）")
    parser.add_argument("--title", help="会議名（分かっていれば指定）")
    parser.add_argument("--attendees", help="出席者（カンマ区切り）")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"エラー: ファイルが見つかりません: {src}", file=sys.stderr)
        return 1
    transcript = src.read_text(encoding="utf-8")
    if not transcript.strip():
        print("エラー: 入力ファイルが空です", file=sys.stderr)
        return 1

    out_path = Path(args.output) if args.output else src.with_name(f"{src.stem}_議事録.md")

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
    print(f"議事録を生成中... (model: {MODEL})", file=sys.stderr)

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(transcript, args.title, args.attendees)}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            response = stream.get_final_message()
    except anthropic.AuthenticationError:
        print("\nエラー: APIキーが無効です。ANTHROPIC_API_KEY を確認してください。", file=sys.stderr)
        return 1
    except anthropic.RateLimitError:
        print("\nエラー: レート制限に達しました。しばらく待って再実行してください。", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"\nエラー: APIエラー ({e.status_code}): {e.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("\nエラー: ネットワーク接続に失敗しました。", file=sys.stderr)
        return 1

    if response.stop_reason == "refusal":
        print("\nエラー: 内容が安全性ポリシーにより処理できませんでした。", file=sys.stderr)
        return 1
    if response.stop_reason == "max_tokens":
        print("\n警告: 出力が上限に達し、途中で切れている可能性があります。", file=sys.stderr)

    minutes = "".join(b.text for b in response.content if b.type == "text")
    out_path.write_text(minutes + "\n", encoding="utf-8")
    print(f"\n\n✅ 議事録を保存しました: {out_path}", file=sys.stderr)
    print(
        f"   使用トークン: 入力 {response.usage.input_tokens:,} / 出力 {response.usage.output_tokens:,}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
