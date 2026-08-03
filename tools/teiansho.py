#!/usr/bin/env python3
"""提案書ドラフト生成ツール — Kaizen AI

顧客ヒアリングメモから、社内テンプレート（templates/proposal-template.md）に
準拠した提案書ドラフト（Markdown）を生成する。

使い方:
    python teiansho.py ヒアリングメモ.txt --client "サンプル商事"
    python teiansho.py ヒアリングメモ.txt --client "サンプル商事" -o 提案書ドラフト.md

事前準備:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...   # キーはコードに書かない
"""

import argparse
import sys
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = REPO_ROOT / "templates" / "proposal-template.md"
SERVICE_CATALOG = REPO_ROOT / "docs" / "services" / "service-catalog.md"

SYSTEM_PROMPT = """\
あなたは業務効率化コンサルティング会社 Kaizen AI の提案書作成の専門家です。
顧客ヒアリングメモから、テンプレートに準拠した提案書のドラフトをMarkdownで作成します。

# ルール
- 与えられたテンプレートの章立て・表の構成に忠実に従う
- ヒアリングメモに書かれている事実だけを根拠にする。書かれていない情報は推測で埋めず、
  該当欄に「（要確認: 〜）」と確認すべき内容を具体的に書く
- 提案するサービス・料金は、参考として与えるサービスカタログのメニュー・価格レンジの範囲内で組み立てる
- 金額は必ず「目安」であることが分かる書き方にする（最終金額はオーナー承認後に確定）
- 「診断から小さく始めて段階的に広げる」進め方を基本として提案する
- 過度な期待を煽る表現（「劇的に」「必ず」「100%」等）は使わない。効果は控えめかつ具体的に
- 顧客の業界・言葉づかいに合わせ、専門用語には短い補足を付ける

# 出力
提案書ドラフトのMarkdown本文のみを出力する（前置き・後書きは不要）。
冒頭に以下の注意書きを含めること:
`> ⚠️ このドラフトはAIが生成した下書きです。金額・スコープはオーナー承認後に確定してください。`
"""


def build_user_prompt(memo: str, client_name: str, template: str, catalog: str | None) -> str:
    parts = [
        f"顧客名: {client_name}",
        f"<template>\n{template}\n</template>",
    ]
    if catalog:
        parts.append(f"<service_catalog>\n{catalog}\n</service_catalog>")
    parts.append(f"<hearing_memo>\n{memo}\n</hearing_memo>")
    parts.append("上記のヒアリングメモをもとに、テンプレートに準拠した提案書ドラフトを作成してください。")
    return "\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="ヒアリングメモから提案書ドラフトを生成する")
    parser.add_argument("input", help="ヒアリングメモのテキストファイル")
    parser.add_argument("--client", required=True, help="顧客名（例: サンプル商事）")
    parser.add_argument("-o", "--output", help="出力先Markdownファイル（省略時: 提案書ドラフト_<顧客名>.md）")
    parser.add_argument("--template", help=f"提案書テンプレート（省略時: {DEFAULT_TEMPLATE}）")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"エラー: ファイルが見つかりません: {src}", file=sys.stderr)
        return 1
    memo = src.read_text(encoding="utf-8")
    if not memo.strip():
        print("エラー: 入力ファイルが空です", file=sys.stderr)
        return 1

    template_path = Path(args.template) if args.template else DEFAULT_TEMPLATE
    if not template_path.exists():
        print(f"エラー: テンプレートが見つかりません: {template_path}", file=sys.stderr)
        return 1
    template = template_path.read_text(encoding="utf-8")

    catalog = SERVICE_CATALOG.read_text(encoding="utf-8") if SERVICE_CATALOG.exists() else None
    if catalog is None:
        print(f"警告: サービスカタログが見つかりません（{SERVICE_CATALOG}）。料金参照なしで生成します。", file=sys.stderr)

    out_path = Path(args.output) if args.output else Path(f"提案書ドラフト_{args.client}.md")

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
    print(f"提案書ドラフトを生成中... (model: {MODEL})", file=sys.stderr)

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(memo, args.client, template, catalog)}],
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

    draft = "".join(b.text for b in response.content if b.type == "text")
    out_path.write_text(draft + "\n", encoding="utf-8")
    print(f"\n\n✅ 提案書ドラフトを保存しました: {out_path}", file=sys.stderr)
    print(
        f"   使用トークン: 入力 {response.usage.input_tokens:,} / 出力 {response.usage.output_tokens:,}",
        file=sys.stderr,
    )
    print("   ⚠️ 内容・金額は必ず確認・承認のうえで顧客に提出してください。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
