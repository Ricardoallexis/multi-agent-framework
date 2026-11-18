const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
 const output = execFileSync('pwsh',['-NoProfile','-File',path.join(__dirname,'test-plans.ps1'),'-EmitFixture'],{encoding:'utf8'});
 const match = output.match(/FIXTURE_BASE64:([A-Za-z0-9+/=]+)/);
 assert.ok(match,'synthetic layout markup was emitted');
 const markup = Buffer.from(match[1],'base64').toString('utf8');
 const browser = await chromium.launch({channel:'chrome',headless:true});
 try {
  for (const colorScheme of ['light','dark']) for (const width of [320,375,1280]) {
   const page = await browser.newPage({viewport:{width,height:900},colorScheme});
   await page.setContent(markup);
   assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,`${colorScheme} ${width}`);
   assert.equal(await page.locator('.plan-usage').count(),3);
   await page.close();
   console.log(`PASS ${colorScheme} ${width}`);
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
