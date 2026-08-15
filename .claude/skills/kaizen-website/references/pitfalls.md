# 実際に踏んだ罠

すべて制作中に実際に発生させ、修正したもの。共通するのは**目視では気づきにくい**か、**気づいたときには理由が分かりにくい**こと。

## レイアウト

### `max-width` を `em` で指定して要素が潰れる

```css
/* ✗ ヒーローが 300px になり、見出しが1文字ずつ折り返した */
.hero-inner { max-width: 20em; }
```

`em` は**その要素自身の font-size** で解決される。`.hero-inner` の font-size は body の 15px なので `20em` = 300px。中の `<h1>` が 116px でも関係ない。「見出し20文字分」のつもりで書くと20倍近く外す。

```css
/* ✓ */
.hero-inner { max-width: min(64vw, 820px); }
```

### SVGアイコンが `width:100%` を拾ってテキストを潰す

```css
.ic { width: 100%; height: 100%; }   /* 円形の枠に収める前提の定義 */
```

この `.ic` を付けたSVGをボタンの中（flex）に置くと、SVGが100%を主張して本文が折り返す。ボタンの文字が3行になっていたら大体これ。

```css
/* ✓ 個別にサイズを固定し、縮まないようにする */
.btn .arw { width: 18px; height: 18px; flex: none; }
```

`width`/`height` 属性を付けても、CSSの方が強いので効かない。

### flex列の中で `inline-block` が横いっぱいに伸びる

`flex-direction: column` の中では既定の `align-items: stretch` が効くため、バッジやタグが横幅いっぱいになる。`align-self: flex-start` を付ける。

### 1pxグリッドの空セルが色の塊になる

```css
/* ✗ 要素数が列数の倍数でないと、空セルにコンテナ背景が出る */
.grid { display: grid; gap: 1px; background: var(--line); }
.cell { background: var(--bg); }
```

目次7件を3列、サービス8件を3列に並べたときに発生。印刷（罫を濃くする）で特に目立つ。

```css
/* ✓ セル側から罫を描く。空セルには何も描かれない */
.grid { display: grid; gap: 1px; background: transparent; }
.cell { box-shadow: 0 0 0 1px var(--line); }
```

### 固定ヘッダーがアンカー先の見出しを隠す

```css
[id] { scroll-margin-top: 110px; }
```

ナビのリンクを踏んだとき、見出しがヘッダーの下に潜る。要素の高さより少し大きめを取る。

### 価格の単位だけが次行に落ちる

`20〜80` と `万円` の間で折り返される。`white-space: nowrap` を掛けたうえで、**列幅に収まる文字サイズまで下げる**。nowrap だけだとはみ出すので両方要る。

### カードの見出しの行数が違って数値の高さが揃わない

価格表など横並びで比較させるものは、見出しに `min-height` で2行分を確保する。バッジも通常フローに置くと1枚だけ下がるので `position: absolute` にする。

## 色

### 面を塗る朱と、文字に使う朱は別の値が要る

`#E8543F` は藍地の上で 4.68（大見出しの基準3.0を満たす）が、**白文字を載せると 3.64** で本文基準4.5を割る。ボタンは最も重要な要素なので、ここを外すと影響が大きい。

- `--shu` `#E8543F` … 文字・罫・図の強調
- `--shu-btn` `#C23D29` … 面の塗り（白文字5.26、藍地との面コントラスト3.24）

色を足したら `scripts/contrast.mjs` を通す。

### ブランド色はグラフの塗りとしては機能しない

データ可視化には**明度と彩度の下限**がある（OKLCH L 0.43–0.77 / C ≥ 0.10）。

| ブランド色 | 問題 | 検証済みの代替 |
|---|---|---|
| `#22335C` 藍 | 明度 0.329 で帯域外・彩度 0.076 で床未満 | `#345BA8` |
| `#C6A868` 真鍮 | 彩度 0.09 で床未満（グレーに寄る） | `#BE9433` |

色相は保っているのでブランドから浮かない。`dataviz` スキルの `scripts/validate_palette.js` で検証すること。真鍮側は地色とのコントラストが 2.74 で3:1を下回るため、**全セグメントへの数値の直接表示**と**同一ページ上の表**の2つで補っている。この2つは外せない。

### SVGの中の色は CSS 変数を経由させる

図をハードコードの `fill="#E8543F"` で描くと、地色を明暗どちらかに変えたときに追従できない。属性ではなく `style="fill:var(--fig-accent)"` にしておき、`.bg-washi, .bg-paper { --fig-accent: var(--shu-btn); }` で切り替える。SVGの属性に `var()` は書けないが、`style` 属性なら効く。

## 日本語まわり

### 縦書きは書体に依存して壊れる

`writing-mode: vertical-rl` は、書体が縦組み用の字送り情報を持たない環境で**文字が重なる**。日本語の明朝体が入っていないLinux等で発生し、漢字だけがビットマップ書体にフォールバックして字送り0になる。

読み込み時に実測して退避する:

```js
const probe = document.createElement('span');
probe.textContent = '改善現場';
probe.style.cssText = 'position:absolute;visibility:hidden;letter-spacing:0;line-height:1';
target.appendChild(probe);
const adv = probe.getBoundingClientRect().height / 4;   // 縦組みなら height が字送り
const fs = parseFloat(getComputedStyle(target).fontSize);
probe.remove();
if (adv < fs * 0.6) document.documentElement.classList.add('no-vert');
```

`.no-vert` 側で `writing-mode: horizontal-tb` に戻す。

### 縦書きのブロックは `inline-block` にする

`display: block` のままだと段が重なる。段の数だけ幅を取らせる必要がある。

### 固有名詞を大文字化しない

`text-transform: uppercase` をラベル用のクラスに付けると、そこに入った顧客名まで変わる。`kikitori` が `KIKITORI` になっていた。英字ラベル用のクラスと、任意の文字列が入るクラスは分ける。

## Canvas

### 半径が負になりうる

`Math.min(w,h)/2 - 8` は、リサイズの途中で要素が小さいときに負になり `arc()` が例外を投げる。`Math.max(0, ...)` で挟み、描画前にも `if (R < 1) return`。

## 印刷

### スクロール演出が非表示のまま印刷される

`opacity: 0` で待機している要素は、印刷時にもそのまま消えている。`@media print` で必ず戻す:

```css
@media print {
  [data-rv], [data-rv-group] > * { opacity: 1 !important; transform: none !important; }
}
```

`scripts/check.mjs` がこれを検出する。

### 縦書き・回転は印刷で崩れる

`writing-mode` と `position: sticky` は印刷で意図しない位置に出る。`@media print` で横書き・`static` に戻す。

## 数値の表示

### `:.0f` は銀行丸め

Python の `f"{62.5:.0f}"` は `62`。四捨五入を期待すると1ずれる。**グラフの表示値は計算させず明示的に書く**のが安全。棒の長さは正確な値、ラベルは意図した丸め方で。
