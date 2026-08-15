#!/usr/bin/env node
/**
 * website/assets/style.css のトークンから、文字色と地色の全組み合わせのWCAGコントラスト比を出す。
 *
 *   node contrast.mjs                    # 既定の組み合わせを検査
 *   node contrast.mjs '#fff' '#C23D29'   # 任意の2色を検査
 *
 * なぜ要るか: このサイトは朱を「文字で使う」場合と「面で塗る」場合があり、
 * 必要な明度が違う。面に明るい朱を使うと白文字が基準を割る（実際に一度やった）。
 * 目視では気づけないので、色を足したら必ずこれを通すこと。
 */
const lin = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const L = hex => {
  const m = hex.replace('#', '').match(/../g).map(h => parseInt(h, 16));
  return 0.2126 * lin(m[0]) + 0.7152 * lin(m[1]) + 0.0722 * lin(m[2]);
};
export const ratio = (a, b) => {
  const l1 = L(a), l2 = L(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
};

const argv = process.argv.slice(2);
if (argv.length === 2) {
  console.log(`${argv[0]} on ${argv[1]} = ${ratio(argv[0], argv[1]).toFixed(2)}`);
  process.exit(0);
}

// style.css の :root と同じ値。CSSを変えたらここも合わせること
const C = {
  sumi: '#080D18', sumi2: '#0C1424', ai: '#111C31', ai2: '#1A2A47',
  washi: '#F2F5FA', paper: '#FFFFFF',
  tx: '#EEF2F9', txm: '#9FAEC6', txl: '#101A2E', txlm: '#57647C',
  kin: '#C6A868', kinw: '#EBDCB4', kinl: '#856929',
  shu: '#E8543F', shuBtn: '#C23D29', white: '#FFFFFF',
};

// [名前, 前景, 背景, 必要比]  4.5=本文/小さい文字, 3.0=大きい文字・UI部品
const TESTS = [
  ['本文（暗地）', 'tx', 'ai', 4.5],
  ['本文（最暗地）', 'tx', 'sumi', 4.5],
  ['補助文（暗地）', 'txm', 'ai', 4.5],
  ['補助文（最暗地）', 'txm', 'sumi', 4.5],
  ['補助文（パネル）', 'txm', 'sumi2', 4.5],
  ['真鍮ラベル（暗地）', 'kin', 'ai', 4.5],
  ['真鍮の見出し（暗地）', 'kinw', 'sumi2', 4.5],
  ['朱の大見出し（暗地）', 'shu', 'ai', 3.0],
  ['ボタンの白文字', 'white', 'shuBtn', 4.5],
  ['ボタンの面 vs 暗地', 'shuBtn', 'ai', 3.0],
  ['本文（紙地）', 'txl', 'washi', 4.5],
  ['補助文（紙地）', 'txlm', 'washi', 4.5],
  ['真鍮ラベル（紙地）', 'kinl', 'washi', 4.5],
  ['朱（紙地・小さい文字）', 'shuBtn', 'washi', 4.5],
];

let bad = 0;
for (const [name, f, b, need] of TESTS) {
  const r = ratio(C[f] ?? f, C[b] ?? b);
  const ok = r >= need;
  if (!ok) bad++;
  console.log(`${ok ? '  ok ' : '  NG '}${r.toFixed(2).padStart(6)}  (要 ${need})  ${name}`);
}
console.log(bad ? `\n${bad} 件が基準未満` : '\n全て基準を満たす');
process.exit(bad ? 1 : 0);
