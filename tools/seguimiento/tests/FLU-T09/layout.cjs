const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
 const browser = await chromium.launch({channel:'chrome',headless:true});
 try {
  for (const colorScheme of ['light','dark']) for (const width of [320,375,1280]) {
   const page = await browser.newPage({viewport:{width,height:900},colorScheme});
   await page.setContent(fs.readFileSync(path.join(__dirname,'fixture.tmp.html'),'utf8'));
   assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,`${colorScheme} ${width}`);
   assert.equal(await page.locator('.plan-usage').count(),3);
   await page.close();
   console.log(`PASS ${colorScheme} ${width}`);
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
