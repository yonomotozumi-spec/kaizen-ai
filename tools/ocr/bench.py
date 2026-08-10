#!/usr/bin/env python3
"""OCRの精度を「自社の実物」で測るベンチマーク — Kaizen AI

同梱サンプルの精度は参考値にすぎない。導入判断に使える数字は、
**顧客の実際の帳票を10〜20枚読ませて測ったもの**だけ。
このスクリプトはそのための計測を機械化する。

手順:
  1. 実際の帳票を1フォルダに集める（scans/）
  2. 正解を手で1回だけ書く（正解.csv。テンプレートは --init で生成）
  3. python3 bench.py scans/ 正解.csv

出力:
  - 正解 / 誤り / 未検出 の件数と率
  - 「無修正で通過した書類の割合」（運用でいちばん効く指標）
  - 誤って出力された値の一覧（未検出と違い、見ただけでは気づけないもの）

正解.csv の書き方:
  - 1行1ファイル。1列目にファイル名、2列目に種別（invoice / receipt / delivery）
  - 3列目以降は項目名を見出しにして正解値を書く
  - 空欄の項目は採点しない（その帳票に存在しない項目）
  - 日付は YYYY-MM-DD、金額は数字のみ（カンマの有無は問わない）
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

UNKNOWN = "（要確認）"
OCR_PY = Path(__file__).with_name("ocr.py")

TEMPLATE_HEADER = ["ファイル名", "種別", "請求書番号", "発行日", "支払期限", "請求先",
                   "合計金額", "小計", "消費税", "登録番号", "電話番号"]


def norm(value: str) -> str:
    """比較用の正規化。空白とカンマの有無、円記号は差とみなさない。"""
    s = re.sub(r"[\s,\\￥¥]", "", str(value or ""))
    return s


def load_truth(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"正解ファイルが空です: {path}")
    if "ファイル名" not in rows[0]:
        sys.exit("正解ファイルの1列目の見出しは「ファイル名」にしてください。")
    return rows


def run_ocr(target: Path, doc_type: str | None) -> dict | None:
    cmd = [sys.executable, str(OCR_PY), str(target), "--json"]
    if doc_type:
        cmd += ["--type", doc_type]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)[0]
    except (json.JSONDecodeError, IndexError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="OCRの精度を自社の実物で測る")
    ap.add_argument("folder", type=Path, nargs="?", help="帳票を集めたフォルダ")
    ap.add_argument("truth", type=Path, nargs="?", help="正解を書いたCSV")
    ap.add_argument("--init", type=Path, metavar="CSV",
                    help="正解CSVのテンプレートを書き出して終了する")
    ap.add_argument("--auto-type", action="store_true",
                    help="種別をocr.pyの自動判定に任せる（既定は正解CSVの種別を使う）")
    ap.add_argument("-o", "--out", type=Path, help="1件ごとの採点結果をCSVに書き出す")
    args = ap.parse_args()

    if args.init:
        with args.init.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(TEMPLATE_HEADER)
            w.writerow(["請求書001.jpg", "invoice", "INV-2026-0413", "2026-07-31",
                        "2026-08-31", "株式会社 山田製作所", "1485000", "1350000",
                        "135000", "T1234567890123", "03-1234-5678"])
            w.writerow(["領収書001.jpg", "receipt", "", "", "", "", "", "", "", "", ""])
        print(f"テンプレートを書き出しました: {args.init}")
        print("2行目の例を消して、実物の正解を書いてください。空欄の項目は採点しません。")
        return

    if not args.folder or not args.truth:
        ap.error("folder と truth を指定してください（テンプレートが要るなら --init 正解.csv）")
    if not args.folder.is_dir():
        sys.exit(f"フォルダが見つかりません: {args.folder}")
    if not args.truth.exists():
        sys.exit(f"正解ファイルが見つかりません: {args.truth}")

    truth_rows = load_truth(args.truth)
    field_names = [k for k in truth_rows[0].keys() if k not in ("ファイル名", "種別")]

    results = []
    for row in truth_rows:
        name = (row.get("ファイル名") or "").strip()
        if not name:
            continue
        target = args.folder / name
        if not target.exists():
            print(f"  見つかりません（スキップ）: {name}", file=sys.stderr)
            continue

        doc_type = None if args.auto_type else (row.get("種別") or "").strip() or None
        got = run_ocr(target, doc_type)
        if got is None:
            print(f"  OCR失敗（スキップ）: {name}", file=sys.stderr)
            continue

        fields = got.get("項目", {})
        ok = wrong = miss = 0
        wrong_list = []
        for key in field_names:
            want = (row.get(key) or "").strip()
            if not want:                      # 空欄は採点しない
                continue
            have = fields.get(key, UNKNOWN)
            if have == UNKNOWN:
                miss += 1
            elif norm(have) == norm(want):
                ok += 1
            else:
                wrong += 1
                wrong_list.append((key, have, want))

        results.append({
            "name": name, "type": got.get("種別", ""), "ok": ok, "wrong": wrong,
            "miss": miss, "wrong_list": wrong_list,
            "warnings": got.get("検算警告", []), "conf": got.get("OCR信頼度", 0),
            "source": got.get("取得元", ""),
        })
        print(f"  {name}: 正解{ok} 誤り{wrong} 未検出{miss}", file=sys.stderr)

    if not results:
        sys.exit("採点できたファイルがありませんでした。")

    total_ok = sum(r["ok"] for r in results)
    total_wrong = sum(r["wrong"] for r in results)
    total_miss = sum(r["miss"] for r in results)
    total = total_ok + total_wrong + total_miss or 1
    clean = [r for r in results if r["wrong"] == 0 and r["miss"] == 0]

    print()
    print("=" * 72)
    print(f"【集計】 {len(results)}件 / 採点項目 {total}個")
    print("=" * 72)
    print(f"  正解    {total_ok:>5}個  ({total_ok / total * 100:.1f}%)")
    print(f"  誤り    {total_wrong:>5}個  ({total_wrong / total * 100:.1f}%)  ← 見ただけでは気づけない")
    print(f"  未検出  {total_miss:>5}個  ({total_miss / total * 100:.1f}%)  ← （要確認）と出るので気づける")
    print()
    print(f"  無修正で通過した書類: {len(clean)}/{len(results)}件 "
          f"({len(clean) / len(results) * 100:.1f}%)  ← 運用でいちばん効く指標")

    ocr_only = [r for r in results if r["source"] == "OCR"]
    if ocr_only:
        avg = sum(r["conf"] for r in ocr_only) / len(ocr_only)
        print(f"  OCR平均信頼度: {avg:.1f}（{len(ocr_only)}件がOCR経由）")

    # 誤読の一覧。検算で捕まえられたかどうかを分けて示す
    wrongs = [r for r in results if r["wrong_list"]]
    print()
    print("=" * 72)
    print("【誤って出力された値】")
    print("=" * 72)
    if not wrongs:
        print("  なし")
    else:
        slipped = 0
        for r in wrongs:
            caught = "検算が検出" if r["warnings"] else "★検算すり抜け"
            if not r["warnings"]:
                slipped += 1
            print(f"  [{r['name']}] ({caught}, 信頼度 {r['conf']:.1f})")
            for key, have, want in r["wrong_list"]:
                print(f"      {key}: 「{have}」 ← 正解「{want}」")
        print()
        print(f"  検算をすり抜けた書類: {slipped}件"
              " ← ここが自動処理の限界。目視確認の対象にする")

    # 信頼度と精度の関係。運用のしきい値を決めるための材料
    print()
    print("=" * 72)
    print("【OCR信頼度ごとの精度】 自動処理に回すしきい値の判断材料")
    print("=" * 72)
    bands = OrderedDict([("85以上", (85, 101)), ("75〜85", (75, 85)),
                         ("60〜75", (60, 75)), ("60未満", (0, 60))])
    print(f"{'信頼度':<10}{'件数':>6}{'正解率':>10}{'誤り率':>10}{'無修正通過':>12}")
    print("-" * 72)
    for label, (lo, hi) in bands.items():
        rs = [r for r in ocr_only if lo <= r["conf"] < hi]
        if not rs:
            continue
        o = sum(r["ok"] for r in rs); w = sum(r["wrong"] for r in rs); m = sum(r["miss"] for r in rs)
        t = o + w + m or 1
        c = sum(1 for r in rs if r["wrong"] == 0 and r["miss"] == 0)
        print(f"{label:<10}{len(rs):>6}{o / t * 100:>9.1f}%{w / t * 100:>9.1f}%{c:>7}/{len(rs)}件")

    if args.out:
        with args.out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ファイル名", "種別", "取得元", "信頼度", "正解", "誤り", "未検出",
                        "誤り内容", "検算警告"])
            for r in results:
                w.writerow([r["name"], r["type"], r["source"], f"{r['conf']:.1f}",
                            r["ok"], r["wrong"], r["miss"],
                            " / ".join(f"{k}:{h}≠{t}" for k, h, t in r["wrong_list"]),
                            " / ".join(r["warnings"])])
        print(f"\n採点結果を書き出しました: {args.out}")

    print()
    print("※ この数字が、顧客に提示してよい唯一の精度です。同梱サンプルの数字は使わないこと。")


if __name__ == "__main__":
    main()
