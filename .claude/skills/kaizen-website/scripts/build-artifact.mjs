#!/usr/bin/env node
/**
 * リポジトリの複数ページ構成から、Artifact 用の単一ファイル版を作る。
 *
 *   node build-artifact.mjs <ページ> "<タイトル>" [置換...] --out <出力先>
 *   例) node build-artifact.mjs cases.html "改善例 — Kaizen AI" \
 *         "index.html=https://claude.ai/code/artifact/xxxx" --out /tmp/cases-artifact.html
 *
 * なぜ要るか: Artifact は外部ファイルを読めない（CSPで外部ホストが全て遮断される）。
 * assets/style.css を参照したままでは無地のページになる。
 * ページ間リンクも相手の Artifact URL に差し替えないと 404 になる。
 *
 * 手順: 相手側のページを先に publish して URL を取り、それを置換に渡してこちらを作る。
 */
import fs from 'fs';
import path from 'path';

const SITE = process.env.KAIZEN_SITE || '/home/user/kaizen-ai/website';
const argv = process.argv.slice(2);
const outAt = argv.indexOf('--out');
const out = outAt >= 0 ? argv[outAt + 1] : null;
const rest = argv.filter((a, i) => i !== outAt && i !== outAt + 1);
const [src, title, ...pairs] = rest;

if (!src || !title || !out) {
  console.error('使い方: node build-artifact.mjs <ページ> "<タイトル>" [from=to ...] --out <出力先>');
  process.exit(1);
}

const css = fs.readFileSync(path.join(SITE, 'assets/style.css'), 'utf8');
const html = fs.readFileSync(path.join(SITE, src), 'utf8');

const a = html.indexOf('<body>');
const b = html.lastIndexOf('</body>');
if (a < 0 || b < 0) throw new Error(`<body> が見つからない: ${src}`);
let body = html.slice(a + 6, b).trim();

for (const p of pairs) {
  const i = p.indexOf('=');
  if (i < 0) continue;
  body = body.split(p.slice(0, i)).join(p.slice(i + 1));
}

// Artifact は <html>/<head>/<body> を自前で付けるので、中身だけを書く
fs.writeFileSync(out, `<title>${title}</title>\n<style>\n${css}</style>\n\n${body}\n`);
console.log(`${out}  ${fs.statSync(out).size} bytes`);
