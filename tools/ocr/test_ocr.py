#!/usr/bin/env python3
"""ocr.py の単体テスト（画像・Tesseract不要）。

    python3 test_ocr.py

金額を扱うツールなので、誤認識の補正規則と検算ロジックは
画像なしで再現できる形にして固定しておく。
"""

import sys
import unittest

from ocr import (
    UNKNOWN,
    detect_doc_type,
    extract_fields,
    extract_line_items,
    fix_invoice_number,
    normalize,
    to_iso_date,
    validate,
)


class TestNormalize(unittest.TestCase):
    def test_桁区切りのピリオド化を戻す(self):
        # 実測で頻出: 1,350,000 が 1.350,000 と読まれる
        self.assertIn("1,350,000", normalize("小計 1.350,000"))
        self.assertIn("1,485,000", normalize("合計 1.485.000"))

    def test_通貨記号のあとの数値に混入した空白を詰める(self):
        # 実測で発生: ￥330,000 が \33 0,000 と読まれる
        self.assertIn("330,000", normalize("金額 \\33 0,000 -"))

    def test_表の列区切りは壊さない(self):
        # 空白2つ以上は列の区切り。数値どうしをつなげてはいけない
        out = normalize("業務診断    1    450,000    450,000")
        self.assertIn("1    450,000", out)

    def test_小文字エルのAIを直す(self):
        self.assertIn("AIワークフロー", normalize("Alワークフロー"))

    def test_通常の英単語は壊さない(self):
        self.assertIn("Also", normalize("Also available"))


class TestDate(unittest.TestCase):
    def test_西暦(self):
        self.assertEqual(to_iso_date("2026年7月31日"), "2026-07-31")

    def test_和暦_令和(self):
        self.assertEqual(to_iso_date("令和8年7月15日"), "2026-07-15")

    def test_和暦_元年(self):
        self.assertEqual(to_iso_date("令和元年5月1日"), "2019-05-01")

    def test_スラッシュ区切り(self):
        self.assertEqual(to_iso_date("2026/8/3"), "2026-08-03")

    def test_解釈できない場合は原文のまま(self):
        self.assertEqual(to_iso_date("翌月末日"), "翌月末日")


class TestInvoiceNumber(unittest.TestCase):
    def test_先頭のTが1に化けたのを戻す(self):
        self.assertEqual(fix_invoice_number("11234567890123"), "T1234567890123")

    def test_Tが落ちた13桁を補う(self):
        self.assertEqual(fix_invoice_number("1234567890123"), "T1234567890123")

    def test_桁数が合わなければ触らない(self):
        self.assertEqual(fix_invoice_number("123"), "123")


class TestDocType(unittest.TestCase):
    def test_表題で判定する(self):
        self.assertEqual(detect_doc_type("請 求 書\n株式会社 御中"), "invoice")
        self.assertEqual(detect_doc_type("納 品 書\n納品日"), "delivery")

    def test_表題が誤認識されても本文で判定する(self):
        # 実測: 「領収書」が「問収書」と読まれた
        text = "問 収 書\n上記正に領収いたしました。\n但し、研修費として"
        self.assertEqual(detect_doc_type(text), "receipt")

    def test_手がかりがなければ請求書として扱う(self):
        self.assertEqual(detect_doc_type("なにもない書類"), "invoice")


class TestExtractFields(unittest.TestCase):
    TEXT = (
        "請 求 書\n"
        "株式会社 山田製作所 御中        株式会社 カイゼンエーアイ\n"
        "請求書番号 : INV-2026-0413      登録番号 : 11234567890123\n"
        "発行日 : 2026年7月31日\n"
        "お支払期限 : 2026年8月31日\n"
        "ご請求金額 \\1,485,000 - (税込)\n"
        "小計                            1,350,000\n"
        "消費税 (10%)                    135,000\n"
    )

    def setUp(self):
        self.f = extract_fields(self.TEXT, "invoice")

    def test_主要項目を取れる(self):
        self.assertEqual(self.f["請求書番号"], "INV-2026-0413")
        self.assertEqual(self.f["発行日"], "2026-07-31")
        self.assertEqual(self.f["支払期限"], "2026-08-31")
        self.assertEqual(self.f["合計金額"], "1,485,000")
        self.assertEqual(self.f["小計"], "1,350,000")
        self.assertEqual(self.f["消費税"], "135,000")

    def test_登録番号を正規化する(self):
        self.assertEqual(self.f["登録番号"], "T1234567890123")

    def test_宛名は御中の直前から取る(self):
        # 1行に宛名と自社名が並んでいても、自社名を巻き込まない
        self.assertEqual(self.f["請求先"], "株式会社 山田製作所")

    def test_お客様は宛名として拾わない(self):
        f = extract_fields("お客様 各位\nご請求金額 \\1,000\n", "invoice")
        self.assertEqual(f["請求先"], UNKNOWN)

    def test_見つからない項目は推測せず要確認にする(self):
        f = extract_fields("請 求 書\n", "invoice")
        self.assertEqual(f["合計金額"], UNKNOWN)
        self.assertEqual(f["発行日"], UNKNOWN)


class TestLineItems(unittest.TestCase):
    def test_空白区切りの明細を拾う(self):
        text = "業務診断 (アセスメント)    1    450,000    450,000\nAIワークフロー構築    1    800,000    800,000\n"
        items = extract_line_items(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][0], "業務診断 (アセスメント)")
        self.assertEqual(items[0][-1], "450,000")

    def test_数値を含まない行は明細にしない(self):
        self.assertEqual(extract_line_items("お振込先    カイゼン銀行    丸の内支店\n"), [])


class TestValidate(unittest.TestCase):
    def test_検算が通れば警告なし(self):
        f = {"小計": "1,350,000", "消費税": "135,000", "合計金額": "1,485,000",
             "発行日": "2026-07-31", "支払期限": "2026-08-31", "登録番号": "T1234567890123"}
        self.assertEqual(validate(f, []), [])

    def test_合計が合わなければ検出する(self):
        # 実測で起きた誤読パターン: 1,350,000 が 1,350,009 になる
        f = {"小計": "1,350,009", "消費税": "135,000", "合計金額": "1,485,000"}
        w = validate(f, [])
        self.assertTrue(any("検算NG" in x for x in w), w)

    def test_明細合計と小計の不一致を検出する(self):
        # 800,000 が 809,000 と誤読されたケース
        items = [["業務診断", "1", "450,000", "450,000"],
                 ["AIワークフロー構築", "1", "800,000", "809,000"],
                 ["操作研修", "2", "50,000", "100,000"]]
        w = validate({"小計": "1,350,000"}, items)
        self.assertTrue(any("明細" in x and "検算NG" in x for x in w), w)

    def test_明細の小計行は二重に数えない(self):
        items = [["業務診断", "1", "450,000", "450,000"],
                 ["AIワークフロー構築", "1", "800,000", "800,000"],
                 ["操作研修", "2", "50,000", "100,000"],
                 ["小計", "", "", "1,350,000"]]
        self.assertEqual(validate({"小計": "1,350,000"}, items), [])

    def test_税率が想定から外れたら注意を出す(self):
        f = {"小計": "1,000,000", "消費税": "300,000", "合計金額": "1,300,000"}
        w = validate(f, [])
        self.assertTrue(any("消費税率" in x for x in w), w)

    def test_日付の前後が逆なら検出する(self):
        w = validate({"発行日": "2026-08-31", "支払期限": "2026-07-31"}, [])
        self.assertTrue(any("支払期限" in x for x in w), w)

    def test_登録番号の形式違反を検出する(self):
        w = validate({"登録番号": "T12345"}, [])
        self.assertTrue(any("形式NG" in x for x in w), w)

    def test_要確認の項目は検算対象にしない(self):
        f = {"小計": UNKNOWN, "消費税": UNKNOWN, "合計金額": "1,485,000", "登録番号": UNKNOWN}
        self.assertEqual(validate(f, []), [])


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
