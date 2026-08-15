#!/usr/bin/env node
/**
 * website/ のページを実際に描画して、目視前に機械で拾える壊れ方を検出する。
 *
 *   node check.mjs [ページ...] [--shots <出力先ディレクトリ>]
 *   例) node check.mjs                       # website/*.html を全部
 *       node check.mjs cases.html --shots /tmp/s
 *
 * 見ているもの:
 *   - JSエラー・コンソールエラー
 *   - 横スクロールの発生（scrollWidth > innerWidth）… clamp/固定幅の事故で頻出
 *   - 画面外にはみ出した要素（右端超過）
 *   - 画面(1440/390)と印刷の3条件
 *   - 印刷時に地色が白へ落ちているか
 *
 * 通れば「壊れていない」だけで、「良い」ではない。最後は必ずスクリーンショットを見ること。
 */
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';
import fs from 'fs';
import path from 'path';

const SITE = process.env.KAIZEN_SITE || '/home/user/kaizen-ai/website';
const args = process.argv.slice(2);
const shotsAt = args.indexOf('--shots');
const shotDir = shotsAt >= 0 ? args[shotsAt + 1] : null;
const pages = args.filter((a, i) => !a.startsWith('--') && i !== shotsAt + 1);
const targets = pages.length ? pages : fs.readdirSync(SITE).filter(f => f.endsWith('.html'));

if (shotDir) fs.mkdirSync(shotDir, { recursive: true });

const VIEWPORTS = [['desktop', 1440, 900], ['mobile', 390, 844]];
let failures = 0;

const browser = await chromium.launch();

for (const file of targets) {
  for (const [label, width, height] of VIEWPORTS) {
    const page = await browser.newPage({ viewport: { width, height } });
    const errors = [];
    page.on('pageerror', e => errors.push('JS: ' + e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

    await page.goto('file://' + path.join(SITE, file), { waitUntil: 'networkidle' });
    // スクロール演出待ちで空白が写らないよう、全部出しておく
    await page.evaluate(() => document.querySelectorAll('[data-rv],[data-rv-group]')
      .forEach(el => el.classList.add('in')));
    await page.waitForTimeout(700);

    const r = await page.evaluate(() => {
      const docW = document.documentElement.scrollWidth;
      const winW = window.innerWidth;
      // 祖先が overflow を持つなら、はみ出しはその中で処理される（断ち落とし・横スクロール）
      const clipped = el => {
        for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
          if (getComputedStyle(p).overflowX !== 'visible') return true;
        }
        return false;
      };
      const over = [...document.body.querySelectorAll('*')]
        .filter(el => {
          const b = el.getBoundingClientRect();
          return b.width > 0 && b.right > winW + 1
            && getComputedStyle(el).position !== 'fixed' && !clipped(el);
        })
        .slice(0, 5)
        .map(el => el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
          ? '.' + el.className.trim().split(/\s+/)[0] : ''));
      return { docW, winW, over };
    });

    const bad = [];
    if (r.docW > r.winW) bad.push(`横スクロール発生 (${r.docW} > ${r.winW})`);
    if (r.over.length) bad.push(`右端をはみ出す要素: ${r.over.join(', ')}`);
    if (errors.length) bad.push(...errors);

    console.log(`${bad.length ? 'NG  ' : 'ok  '}${file} @${label}` + (bad.length ? '\n      ' + bad.join('\n      ') : ''));
    if (bad.length) failures++;

    if (shotDir) {
      await page.screenshot({ path: path.join(shotDir, `${file.replace('.html', '')}-${label}.png`), fullPage: true });
    }
    await page.close();
  }

  // 印刷
  const page = await browser.newPage({ viewport: { width: 794, height: 1123 } });
  await page.goto('file://' + path.join(SITE, file), { waitUntil: 'networkidle' });
  await page.emulateMedia({ media: 'print' });
  await page.waitForTimeout(400);
  const p = await page.evaluate(() => {
    const bg = getComputedStyle(document.body).backgroundColor;
    const dark = [...document.querySelectorAll('.band,.hero,.cta-band,.page-head')]
      .map(e => getComputedStyle(e).backgroundColor)
      .filter(c => { const m = c.match(/\d+/g); return m && (+m[0] + +m[1] + +m[2]) < 600; });
    const hidden = [...document.querySelectorAll('[data-rv],[data-rv-group] > *')]
      .filter(e => +getComputedStyle(e).opacity < 0.5).length;
    return { bg, darkBands: dark.length, invisible: hidden };
  });
  const pbad = [];
  if (p.darkBands) pbad.push(`印刷で暗いままの帯が ${p.darkBands} 件`);
  if (p.invisible) pbad.push(`印刷で非表示のままの要素が ${p.invisible} 件（スクロール演出の消し忘れ）`);
  console.log(`${pbad.length ? 'NG  ' : 'ok  '}${file} @print` + (pbad.length ? '\n      ' + pbad.join('\n      ') : ''));
  if (pbad.length) failures++;
  if (shotDir) await page.screenshot({ path: path.join(shotDir, `${file.replace('.html', '')}-print.png`) });
  await page.close();
}

await browser.close();
console.log(failures ? `\n${failures} 件の問題` : '\nすべて通過（描画は要目視）');
process.exit(failures ? 1 : 0);
