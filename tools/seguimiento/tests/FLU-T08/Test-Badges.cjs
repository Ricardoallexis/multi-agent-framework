// FLU-T08: browser layout regression using the viewer's real stylesheet and synthetic cards.
// Run with Node and Playwright installed (PLAYWRIGHT_MODULE may point at a local installation).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const source = fs.readFileSync(path.resolve(__dirname, '../../ver-seguimiento.ps1'), 'utf8');
const css = source.match(/<style>([\s\S]*?)<\/style>/)[1];
const long = 'Active · working for Example-Orchestrator on Planes de implementación días 2-3 (Worker) ';
const badge = text => `<span class="state s-en-curso">${text}</span>`;
const html = `<style>${css}</style><div class="agent-grid"><section class="panel agent-card">
  <div class="card-head"><h2>Example</h2>${badge(long.repeat(3))}</div>
  <div class="agent-name"><div class="card-head"><h3>Example-Agent</h3>${badge('Active')}</div>
  <div class="agent-task">${badge(long.repeat(3))}</div>
  <div class="agent-task">${badge('x'.repeat(240))}</div></div>
</section></div>`;
(async () => {
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  try {
    for (const colorScheme of ['light', 'dark']) {
      for (const width of [320, 375, 1280]) {
        const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme });
        await page.setContent(html);
        const result = await page.evaluate(() => {
          const issues = [];
          for (const chip of document.querySelectorAll('.state')) {
            const box = chip.getBoundingClientRect();
            const parent = chip.closest('.agent-task, .card-head').getBoundingClientRect();
            if (box.left < parent.left - 1 || box.right > parent.right + 1 || chip.scrollWidth > chip.clientWidth + 1) issues.push(chip.textContent);
          }
          const chips = [...document.querySelectorAll('.state')];
          return { issues, short: chips[1].offsetHeight, long: chips[0].offsetHeight,
            pageOverflow: document.documentElement.scrollWidth > innerWidth };
        });
        assert.deepEqual(result.issues, [], `${colorScheme} ${width}: chips fit their boxes`);
        assert.equal(result.pageOverflow, false, `${colorScheme} ${width}: grid fits viewport`);
        assert.ok(result.long > result.short, 'long badge wraps; short badge stays on one line');
        await page.close();
        console.log(`PASS ${colorScheme} ${width}`);
      }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
