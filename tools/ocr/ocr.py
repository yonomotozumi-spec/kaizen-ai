#!/usr/bin/env python3
"""
紙の書類 → データ化ツール（OCR / 帳票読み取り）— Kaizen AI

生成AI（LLM）を一切使わないため、APIトークンの消費はゼロ。
すべてローカルで完結し、書類が外部に送信されることもない。

処理の流れ:
  1. PDFにテキスト層があれば OCR せずに直接抽出（最速・誤認識ゼロ）
  2. テキスト層がなければ画像化 → 前処理 → Tesseract OCR
     前処理: 拡大 → 傾き補正 → 二値化 → 罫線除去
     ※ 罫線を消さないと表のセルが潰れて読めない（これが精度の分かれ目）
  3. OCR特有の誤認識を規則で補正（例: 1.350,000 → 1,350,000）
  4. 正規表現ベースで帳票の項目を抽出（LLM不使用）
  5. CSV / JSON / テキストで出力

使い方:
  python3 ocr.py 請求書.pdf                      # 1ファイルを読む
  python3 ocr.py scans/ -o 結果.csv              # フォルダを一括処理してCSVに
  python3 ocr.py 請求書.jpg --type invoice --json
  python3 ocr.py 書類.pdf --text-only            # 生テキストだけ欲しいとき

セットアップは README.md を参照。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:
    sys.exit("cv2 / numpy が見つかりません。`pip install -r requirements.txt` を実行してください。")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_EXT = IMAGE_EXT | {".pdf"}

UNKNOWN = "（要確認）"   # 推測で埋めない。既存ツール（gijiroku.py / teiansho.py）と同じ方針


# ============================================================
# 外部コマンドの確認
# ============================================================
def require_tools(need_pdf: bool) -> None:
    missing = []
    if not shutil.which("tesseract"):
        missing.append("tesseract（sudo apt install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert）")
    if need_pdf and not shutil.which("pdftoppm"):
        missing.append("poppler-utils（sudo apt install poppler-utils）")
    if missing:
        sys.exit("必要なコマンドが見つかりません:\n  - " + "\n  - ".join(missing))


def tesseract_langs() -> set[str]:
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
        return {l.strip() for l in out.splitlines()[1:] if l.strip()}
    except Exception:
        return set()


# ============================================================
# 画像の前処理
# ============================================================
def deskew(gray: np.ndarray, max_deg: float = 5.0) -> tuple[np.ndarray, float]:
    """文字塊の最小外接矩形から傾きを推定して水平に戻す。"""
    inverted = 255 - gray
    coords = np.column_stack(np.where(inverted > 60))
    if len(coords) < 100:
        return gray, 0.0
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    if abs(angle) > max_deg or abs(angle) < 0.05:
        return gray, 0.0
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
    return rotated, angle


def remove_rules(gray: np.ndarray) -> np.ndarray:
    """表の罫線を検出して塗りつぶす。

    罫線が残っているとTesseractがセルを1文字として扱い、表の中身がほぼ全滅する。
    横長・縦長の構造要素でモルフォロジー処理し、線だけを取り出して inpaint で消す。
    """
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )
    h, w = bw.shape
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 30), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 30)))
    horizontal = cv2.dilate(cv2.erode(bw, h_kernel), h_kernel)
    vertical = cv2.dilate(cv2.erode(bw, v_kernel), v_kernel)
    lines = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8), iterations=1)
    if cv2.countNonZero(lines) == 0:
        return gray
    return cv2.inpaint(gray, lines, 3, cv2.INPAINT_TELEA)


def preprocess(path: Path, keep_rules: bool = False, min_width: int = 1800) -> tuple[np.ndarray, dict]:
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"画像を読み込めません: {path}")

    info: dict = {"元サイズ": f"{gray.shape[1]}x{gray.shape[0]}"}

    # 解像度が低いとTesseractの精度が大きく落ちる。短辺基準で拡大する。
    if gray.shape[1] < min_width:
        scale = min(3.0, min_width / gray.shape[1])
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        info["拡大率"] = f"{scale:.2f}x"

    gray, angle = deskew(gray)
    info["傾き補正"] = f"{angle:+.2f}°"

    if not keep_rules:
        gray = remove_rules(gray)
        info["罫線除去"] = "あり"

    return gray, info


# ============================================================
# OCR 実行
# ============================================================
def run_tesseract(image: np.ndarray, lang: str, psm: int) -> tuple[str, float]:
    """OCRを実行し、テキストと平均信頼度（0〜100）を返す。"""
    with tempfile.TemporaryDirectory() as td:
        img_path = os.path.join(td, "page.png")
        cv2.imwrite(img_path, image)
        base = os.path.join(td, "out")
        cmd = ["tesseract", img_path, base, "-l", lang, "--psm", str(psm),
               "-c", "preserve_interword_spaces=1", "txt", "tsv"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract 失敗: {proc.stderr.strip()}")

        text = Path(base + ".txt").read_text(encoding="utf-8", errors="replace")

        confidences = []
        tsv_path = Path(base + ".tsv")
        if tsv_path.exists():
            for line in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                cols = line.split("\t")
                if len(cols) >= 12 and cols[11].strip():
                    try:
                        c = float(cols[10])
                    except ValueError:
                        continue
                    if c >= 0:
                        confidences.append(c)
        conf = sum(confidences) / len(confidences) if confidences else 0.0
    return text, conf


def pdf_text_layer(path: Path) -> str | None:
    """PDFに埋め込みテキストがあれば取り出す。あればOCRは不要（誤認識ゼロ）。"""
    if not shutil.which("pdftotext"):
        return None
    proc = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    text = proc.stdout
    # 文字数が極端に少ないものはスキャンPDF（画像のみ）とみなす
    return text if len(re.sub(r"\s", "", text)) >= 40 else None


def pdf_to_images(path: Path, workdir: str, dpi: int, max_pages: int) -> list[Path]:
    prefix = os.path.join(workdir, "page")
    cmd = ["pdftoppm", "-r", str(dpi), "-gray", "-png"]
    if max_pages:
        cmd += ["-l", str(max_pages)]
    cmd += [str(path), prefix]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pdftoppm 失敗: {proc.stderr.strip()}")
    return sorted(Path(workdir).glob("page*.png"))


# ============================================================
# OCR結果の正規化（誤認識の規則補正）
# ============================================================
def normalize(text: str) -> str:
    """Tesseract日本語で頻出する誤認識を、規則で機械的に直す。

    ここは「よくある崩れ」を潰すだけで、意味の推測はしない。
    誤った補正を避けるため、パターンは十分に限定的なものだけを入れている。
    """
    out = text

    # 全角英数字・全角記号 → 半角（金額や番号の比較を可能にするため）
    out = "".join(
        unicodedata.normalize("NFKC", ch) if unicodedata.east_asian_width(ch) in "FA" and ch.isascii() is False
        and unicodedata.normalize("NFKC", ch).isascii() else ch
        for ch in out
    )

    # 数値の桁区切りが . ・ 。 、 に化ける（1.350,000 / 638。000 → 1,350,000 / 638,000）
    def fix_sep(m: re.Match) -> str:
        return re.sub(r"[.・。、 ]", ",", m.group(0))
    out = re.sub(r"\d{1,3}(?:[.,・。、]\d{3})+(?![\d.])", fix_sep, out)

    # 円記号の直後の数値に空白が入り込む（\33 0,000 → \330,000）。
    # 表の列区切り（空白2つ以上）を壊さないよう、通貨記号に続く並びだけを対象にする。
    out = re.sub(r"([\\￥¥])\s*((?:\d[\d, ]{0,18}\d|\d))",
                 lambda m: m.group(1) + re.sub(r" (?=[\d,])", "", m.group(2)), out)

    # 「Al」「AI」の取り違え（小文字L）。Alで始まる英単語より業務文書ではAIが圧倒的に多い
    out = re.sub(r"\bAl(?=[^a-z]|$)", "AI", out)

    # 円記号のゆれ
    out = out.replace("￥", "\\").replace("¥", "\\")

    # 全角スペースの連続を半角に寄せる（列の区切り判定を安定させる）
    out = out.replace("　", "  ")

    return out


def fix_invoice_number(value: str) -> str:
    """インボイス登録番号は T + 13桁。先頭のTが 1 / I / l / | に化けやすいので戻す。"""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 14 and digits[0] in "17":
        return "T" + digits[1:]
    if len(digits) == 13:
        return "T" + digits
    return value


# ============================================================
# 帳票フィールドの抽出（正規表現ベース / LLM不使用）
# ============================================================
WAREKI = {"令和": 2018, "平成": 1988, "昭和": 1925}


def to_iso_date(raw: str) -> str:
    """和暦・漢数字区切り・スラッシュ区切りを YYYY-MM-DD に寄せる。"""
    s = raw.strip()
    m = re.search(r"(令和|平成|昭和)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        era, y, mo, d = m.groups()
        year = WAREKI[era] + (1 if y == "元" else int(y))
        return f"{year:04d}-{int(mo):02d}-{int(d):02d}"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return raw.strip()


def to_amount(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", raw)
    return digits or ""


# 帳票テンプレート: (項目名, 正規表現, 後処理)
# 「ラベル → 同じ行の値」を拾う。複数ヒットした場合は最初のものを採用する。
TEMPLATES: dict[str, list[tuple[str, str, str]]] = {
    "invoice": [
        ("請求書番号", r"(?:請求書?番号|invoice\s*no\.?|No\.?)\s*[:：]?\s*([A-Za-z0-9\-_/]{3,30})", "raw"),
        ("発行日",     r"(?:発行日|請求日|作成日|日付)\s*[:：]?\s*([^\n]{6,24})", "date"),
        ("支払期限",   r"(?:お?支払期限|支払期日|振込期限|お支払い期限)\s*[:：]?\s*([^\n]{6,24})", "date"),
        # 宛名は「御中」の直前。1行に宛名と自社名が並ぶ帳票が多いので、
        # 行頭または空白2つ以上（＝列の切れ目）を起点に拾う。
        ("請求先",     r"(?:^|\s{2,})([^\n]{2,30}?)\s*(?:御中|様)(?:\s|$)", "name"),
        ("合計金額",   r"(?:ご?請求金額|合計金額|お支払金額|税込合計|請求額)\s*[:：]?\s*[\\￥¥]?\s*([\d,]{3,15})", "amount"),
        ("小計",       r"(?:小\s*計)\s*[:：]?\s*[\\￥¥]?\s*([\d,]{3,15})", "amount"),
        ("消費税",     r"(?:消費税|税額)\s*(?:\([^)]*\)|（[^）]*）)?\s*[:：]?\s*[\\￥¥]?\s*([\d,]{2,15})", "amount"),
        ("登録番号",   r"(?:登録番号|適格請求書発行事業者登録番号|インボイス番号)\s*[:：]?\s*([T1Il|][ \t]?\d[\d \t\-]{10,18})", "tnum"),
        ("電話番号",   r"(?:TEL|Tel|電話|℡)\s*[:：]?\s*([\d\-\(\)\s]{9,18})", "tel"),
        ("振込先",     r"(?:お?振込先|振込口座|お振り込み先)\s*[:：]?\s*([^\n]{5,60})", "raw"),
    ],
    "receipt": [
        ("日付",       r"(?:発行日|日付|)\s*[:：]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|(?:令和|平成)\s*\d{1,2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2})", "date"),
        ("金額",       r"(?:金額|合計|領収金額|)\s*[\\￥¥]\s*([\d,]{3,15})", "amount"),
        ("但し書き",   r"但\s*し?\s*[,、]?\s*([^\n]{2,40}?)\s*(?:として|の?代金|$)", "raw"),
        ("発行者",     r"(?:発行者|店舗名|)\s*((?:株式会社|有限会社|合同会社)[^\s\n]{1,20})", "raw"),
        ("登録番号",   r"(?:登録番号|インボイス番号)\s*[:：]?\s*([T1Il|][ \t]?\d[\d \t\-]{10,18})", "tnum"),
    ],
    "delivery": [
        ("納品書番号", r"(?:納品書?番号|No\.?)\s*[:：]?\s*([A-Za-z0-9\-_/]{3,30})", "raw"),
        ("納品日",     r"(?:納品日|出荷日|日付)\s*[:：]?\s*([^\n]{6,24})", "date"),
        ("納品先",     r"(?:^|\s{2,})([^\n]{2,30}?)\s*(?:御中|様)(?:\s|$)", "name"),
        ("合計金額",   r"(?:合計|合計金額)\s*[:：]?\s*[\\￥¥]?\s*([\d,]{3,15})", "amount"),
    ],
}

# 種別判定の手がかり。表題は文字間隔が広く大きいためOCRが崩しやすい
# （実測でも「領収書」→「問収書」と誤読された）ので、本文側の語も併せて数える。
DOC_HINTS: dict[str, list[tuple[str, int]]] = {
    "invoice": [(r"請\s*求\s*書", 3), (r"ご?請求金額", 2), (r"請求書?番号", 2),
                (r"お?支払期限|支払期日", 2), (r"御請求", 1)],
    "receipt": [(r"領\s*収\s*書", 3), (r"レシート", 3), (r"領収いた", 2),
                (r"上記正に", 2), (r"但\s*し[、,]", 1), (r"収入印紙", 1)],
    "delivery": [(r"納\s*品\s*書", 3), (r"納品日", 2), (r"納品先", 2), (r"出荷日", 1)],
}


def detect_doc_type(text: str) -> str:
    scores = {
        name: sum(w for pattern, w in hints if re.search(pattern, text))
        for name, hints in DOC_HINTS.items()
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "invoice"   # 手がかりゼロなら請求書テンプレートで試す


def extract_fields(text: str, doc_type: str) -> dict[str, str]:
    rules = TEMPLATES.get(doc_type, TEMPLATES["invoice"])
    result: dict[str, str] = {}
    for name, pattern, kind in rules:
        value = UNKNOWN
        for m in re.finditer(pattern, text, re.MULTILINE):
            captured = m.group(1).strip()
            if not captured:
                continue
            if kind == "date":
                captured = to_iso_date(captured)
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", captured):
                    continue
            elif kind == "amount":
                captured = to_amount(captured)
                if not captured:
                    continue
                captured = f"{int(captured):,}"
            elif kind == "tnum":
                captured = fix_invoice_number(captured)
            elif kind == "tel":
                captured = re.sub(r"[\s()]", "", captured)
                if len(re.sub(r"\D", "", captured)) < 9:
                    continue
            elif kind == "name":
                # 「お客様」「皆様」「関係者各位」等は宛名ではないので拾わない
                if re.search(r"(お客|皆|各位|担当者)$", captured):
                    continue
            elif kind == "label":
                captured = re.sub(r"\s", "", captured)
            value = captured
            break
        result[name] = value
    return result


# ============================================================
# 明細行の抽出（表）
# ============================================================
def extract_line_items(text: str, min_cols: int = 3) -> list[list[str]]:
    """空白2つ以上を列区切りとみなし、数値列を含む行を明細として拾う。

    OCRは `preserve_interword_spaces=1` で列間の空白を保持しているため、
    罫線を消した後でも列の並びは空白として残っている。
    """
    items: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]
        if len(cols) < min_cols:
            continue
        numeric = sum(1 for c in cols if re.fullmatch(r"[\\￥¥]?[\d,]+[-‑—]?", c))
        if numeric >= max(1, len(cols) - 2) and any(re.search(r"\d", c) for c in cols):
            items.append(cols)
    return items


# ============================================================
# 検算（誤認識の自動検出）
# ============================================================
def _amount(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else None


def validate(fields: dict[str, str], line_items: list[list[str]]) -> list[str]:
    """抽出結果に対して規則ベースの整合性チェックをかける。

    OCRで怖いのは未検出ではなく「もっともらしい誤読」（800,000 → 809,000 など）。
    未検出は（要確認）で気づけるが、誤読は見た目では気づけない。
    帳票が本来持っている冗長性（小計＋税＝合計 など）を使って機械的に炙り出す。
    """
    warnings: list[str] = []

    total = _amount(fields.get("合計金額", ""))
    subtotal = _amount(fields.get("小計", ""))
    tax = _amount(fields.get("消費税", ""))

    if total is not None and subtotal is not None and tax is not None:
        if subtotal + tax != total:
            warnings.append(
                f"検算NG: 小計 {subtotal:,} + 消費税 {tax:,} = {subtotal + tax:,} ≠ 合計 {total:,}"
                "（いずれかを誤認識している可能性）")
        else:
            rate = tax / subtotal if subtotal else 0
            if not (0.07 < rate < 0.11):
                warnings.append(f"検算注意: 消費税率が {rate * 100:.1f}% と算出されました（8%/10%から乖離）")

    # 明細の最終列（金額）の合計と小計を突き合わせる
    if subtotal is not None and len(line_items) >= 2:
        line_sum = 0
        counted = 0
        for row in line_items:
            if re.match(r"^(小\s*計|合\s*計|消費税|税)", row[0]):
                continue
            v = _amount(row[-1])
            if v:
                line_sum += v
                counted += 1
        if counted >= 2 and line_sum != subtotal:
            warnings.append(
                f"検算NG: 明細{counted}行の金額合計 {line_sum:,} ≠ 小計 {subtotal:,}"
                "（明細の桁の誤認識、または明細の取りこぼし）")

    # 日付の前後関係
    issue, due = fields.get("発行日", ""), fields.get("支払期限", "")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", issue) and re.match(r"^\d{4}-\d{2}-\d{2}$", due):
        if due < issue:
            warnings.append(f"検算NG: 支払期限 {due} が発行日 {issue} より前になっています")

    # インボイス登録番号は T + 13桁
    reg = fields.get("登録番号", "")
    if reg and reg != UNKNOWN and not re.fullmatch(r"T\d{13}", reg):
        warnings.append(f"形式NG: 登録番号 '{reg}' が「T+13桁」になっていません")

    return warnings


# ============================================================
# 1ファイルの処理
# ============================================================
@dataclass
class Result:
    path: Path
    doc_type: str
    source: str                  # "PDFテキスト層" or "OCR"
    text: str
    fields: dict[str, str] = field(default_factory=dict)
    line_items: list[list[str]] = field(default_factory=list)
    confidence: float = 0.0
    pages: int = 1
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def process_file(path: Path, args) -> Result:
    notes: list[str] = []
    source = "OCR"
    confidence = 0.0
    pages = 1

    if path.suffix.lower() == ".pdf":
        raw = None if args.force_ocr else pdf_text_layer(path)
        if raw:
            source = "PDFテキスト層"
            confidence = 100.0
            notes.append("PDFに埋め込みテキストがあったためOCRを実行していません（誤認識なし）")
        else:
            with tempfile.TemporaryDirectory() as td:
                images = pdf_to_images(path, td, args.dpi, args.max_pages)
                pages = len(images)
                if not images:
                    raise RuntimeError("PDFをページ画像に変換できませんでした")
                chunks, confs = [], []
                for img_path in images:
                    prepared, info = preprocess(img_path, args.keep_rules, args.min_width)
                    t, c = run_tesseract(prepared, args.lang, args.psm)
                    chunks.append(t)
                    confs.append(c)
                raw = "\n".join(chunks)
                confidence = sum(confs) / len(confs) if confs else 0.0
                notes.append(f"スキャンPDFのためOCRを実行（{pages}ページ / {args.dpi}dpi）")
    else:
        prepared, info = preprocess(path, args.keep_rules, args.min_width)
        raw, confidence = run_tesseract(prepared, args.lang, args.psm)
        notes.append("画像OCR " + " / ".join(f"{k}:{v}" for k, v in info.items()))

    text = normalize(raw)
    doc_type = args.type if args.type != "auto" else detect_doc_type(text)
    fields = extract_fields(text, doc_type)
    items = extract_line_items(text)

    warnings = validate(fields, items)

    if confidence and confidence < args.warn_confidence:
        notes.append(f"OCR信頼度が低い（平均 {confidence:.1f}）。原本との突合を推奨")
    missing = [k for k, v in fields.items() if v == UNKNOWN]
    if missing:
        notes.append("未検出の項目: " + " / ".join(missing))

    return Result(path=path, doc_type=doc_type, source=source, text=text,
                  fields=fields, line_items=items, confidence=confidence,
                  pages=pages, notes=notes, warnings=warnings)


# ============================================================
# 出力
# ============================================================
DOC_LABEL = {"invoice": "請求書", "receipt": "領収書", "delivery": "納品書"}


def print_report(r: Result) -> None:
    print("=" * 62)
    print(f"■ {r.path.name}")
    print(f"  種別: {DOC_LABEL.get(r.doc_type, r.doc_type)} / 取得元: {r.source}"
          + (f" / OCR信頼度: {r.confidence:.1f}" if r.source == "OCR" else ""))
    print("-" * 62)
    for k, v in r.fields.items():
        mark = "  " if v != UNKNOWN else "！ "
        print(f"{mark}{k:<8}: {v}")
    if r.line_items:
        print("-" * 62)
        print("  明細（推定）:")
        for row in r.line_items:
            print("    " + " | ".join(row))
    if r.warnings:
        print("-" * 62)
        for w in r.warnings:
            print(f"  ⚠ {w}")
    if r.notes:
        print("-" * 62)
        for n in r.notes:
            print(f"  ※ {n}")
    print()


def write_csv(results: list[Result], out_path: Path, quiet: bool = False) -> None:
    keys: list[str] = []
    for r in results:
        for k in r.fields:
            if k not in keys:
                keys.append(k)
    header = (["要確認", "ファイル名", "種別", "取得元", "OCR信頼度"] + keys
              + ["明細行数", "未検出項目", "検算警告"])
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in results:
            missing = [k for k, v in r.fields.items() if v == UNKNOWN]
            needs_review = "要確認" if (missing or r.warnings) else ""
            w.writerow(
                [needs_review, r.path.name, DOC_LABEL.get(r.doc_type, r.doc_type), r.source,
                 f"{r.confidence:.1f}" if r.source == "OCR" else ""]
                + [r.fields.get(k, "") for k in keys]
                + [len(r.line_items), " / ".join(missing), " / ".join(r.warnings)]
            )
    print(f"CSVを書き出しました: {out_path}", file=sys.stderr if quiet else sys.stdout)


def write_items_csv(results: list[Result], out_path: Path, quiet: bool = False) -> None:
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ファイル名", "行番号", "列1", "列2", "列3", "列4", "列5"])
        for r in results:
            for i, row in enumerate(r.line_items, 1):
                w.writerow([r.path.name, i] + (row + [""] * 5)[:5])
    print(f"明細CSVを書き出しました: {out_path}", file=sys.stderr if quiet else sys.stdout)


# ============================================================
# エントリポイント
# ============================================================
def collect_inputs(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    files = sorted(p for p in target.rglob("*") if p.suffix.lower() in SUPPORTED_EXT)
    return files


def main() -> None:
    ap = argparse.ArgumentParser(
        description="紙の書類をデータ化する（OCR / 帳票読み取り）。LLM不使用のためトークン消費はゼロ。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n"
               "  python3 ocr.py 請求書.pdf\n"
               "  python3 ocr.py scans/ -o 結果.csv --items 明細.csv\n"
               "  python3 ocr.py 領収書.jpg --type receipt --json\n")
    ap.add_argument("input", type=Path, help="画像/PDFファイル、またはそれらを含むフォルダ")
    ap.add_argument("-o", "--out", type=Path, help="抽出項目のCSV出力先")
    ap.add_argument("--items", type=Path, help="明細行のCSV出力先")
    ap.add_argument("--json", action="store_true", help="JSONで標準出力に出す")
    ap.add_argument("--text-only", action="store_true", help="項目抽出をせず、認識テキストだけを出す")
    ap.add_argument("--save-text", type=Path, help="認識テキストを保存するフォルダ")
    ap.add_argument("--type", default="auto", choices=["auto", "invoice", "receipt", "delivery"],
                    help="帳票の種類（既定: auto = 本文から自動判定）")
    ap.add_argument("--lang", default="jpn", help="Tesseractの言語（既定: jpn。英日混在なら jpn+eng）")
    ap.add_argument("--psm", type=int, default=6, help="Tesseractのページ分割モード（既定: 6）")
    ap.add_argument("--dpi", type=int, default=300, help="PDFを画像化するときの解像度（既定: 300）")
    ap.add_argument("--max-pages", type=int, default=0, help="PDFの読み取りページ数の上限（0=全ページ）")
    ap.add_argument("--min-width", type=int, default=1800, help="この幅より小さい画像は拡大する（既定: 1800px）")
    ap.add_argument("--keep-rules", action="store_true", help="罫線を除去しない（表がない書類向け）")
    ap.add_argument("--force-ocr", action="store_true", help="PDFにテキスト層があっても強制的にOCRする")
    ap.add_argument("--warn-confidence", type=float, default=75.0, help="この信頼度を下回ったら警告（既定: 75）")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"入力が見つかりません: {args.input}")

    files = collect_inputs(args.input)
    if not files:
        sys.exit(f"対象ファイルがありません（対応: {', '.join(sorted(SUPPORTED_EXT))}）")

    require_tools(need_pdf=any(f.suffix.lower() == ".pdf" for f in files))

    langs = tesseract_langs()
    for code in args.lang.split("+"):
        if langs and code not in langs:
            sys.exit(f"Tesseractに言語データ '{code}' がありません。導入済み: {', '.join(sorted(langs))}\n"
                     f"  例) sudo apt install tesseract-ocr-jpn tesseract-ocr-jpn-vert")

    results: list[Result] = []
    for i, path in enumerate(files, 1):
        if len(files) > 1:
            print(f"[{i}/{len(files)}] {path.name} …", file=sys.stderr)
        try:
            r = process_file(path, args)
        except Exception as e:
            print(f"  スキップ（{path.name}）: {e}", file=sys.stderr)
            continue
        results.append(r)

        if args.save_text:
            args.save_text.mkdir(parents=True, exist_ok=True)
            (args.save_text / (path.stem + ".txt")).write_text(r.text, encoding="utf-8")

    if not results:
        sys.exit("処理できたファイルがありませんでした。")

    if args.text_only:
        for r in results:
            print(f"===== {r.path.name} =====")
            print(r.text)
        return

    if args.json:
        payload = [{
            "ファイル名": r.path.name, "種別": DOC_LABEL.get(r.doc_type, r.doc_type),
            "取得元": r.source, "OCR信頼度": round(r.confidence, 1),
            "項目": r.fields, "明細": r.line_items,
            "検算警告": r.warnings, "注記": r.notes,
        } for r in results]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print_report(r)

    if args.out:
        write_csv(results, args.out, quiet=args.json)
    if args.items:
        write_items_csv(results, args.items, quiet=args.json)

    ocr_done = [r for r in results if r.source == "OCR"]
    review = [r for r in results
              if r.warnings or any(v == UNKNOWN for v in r.fields.values())]

    # --json は他システムへの受け渡し用なので、標準出力にはJSON以外を混ぜない
    out = sys.stderr if args.json else sys.stdout

    if ocr_done:
        avg = sum(r.confidence for r in ocr_done) / len(ocr_done)
        print(f"処理 {len(results)}件（うちOCR {len(ocr_done)}件 / 平均信頼度 {avg:.1f}）", file=out)
    else:
        print(f"処理 {len(results)}件（すべてPDFのテキスト層から取得）", file=out)

    if review:
        print(f"要確認 {len(review)}件: " + " / ".join(r.path.name for r in review), file=out)
    print("※ 検算を通った項目も誤認識の可能性は残ります。金額・日付・口座番号は原本と突合してください。",
          file=out)


if __name__ == "__main__":
    main()
