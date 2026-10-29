const {chromium} = require('playwright');
const assert = require('assert');
(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  const page = await browser.newPage();
  const errors = [], external = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('request', r => {if (!r.url().startsWith('http://127.0.0.1:8000') && !r.url().startsWith('data:')) external.push(r.url());});
  await page.goto('http://127.0.0.1:8000', {waitUntil:'networkidle'});
  await page.getByText('Live sample demo — real Supervisely resources', {exact:true}).waitFor();
  await page.getByText('tomas.bf.santos@gmail.com', {exact:true}).waitFor();
  assert(await page.getByRole('tab').count() === 3);
  assert(await page.getByRole('button', {name:'Prepare working presentation',exact:true}).count() === 0);
  assert(await page.getByRole('combobox', {name:'Demo role',exact:true}).count() === 0);
  assert(await page.getByRole('button', {name:'Simulate participant submission',exact:true}).count() === 0);
  await page.getByRole('heading', {name:'Set up the demo event',exact:true}).waitFor();
  assert(await page.getByRole('button', {name:'Set up sample event',exact:true}).isDisabled());
  assert(await page.getByRole('button', {name:'Initialize event',exact:true}).isDisabled());
  await page.getByText('Connect account first to enable this button.', {exact:false}).waitFor();
  const status = await (await page.request.get('http://127.0.0.1:8000/healthz')).json();
  assert.deepStrictEqual(status, {status:'ok',mode:process.env.EXPECTED_APP_MODE});
  const denied = await page.request.post('http://127.0.0.1:8000/setup/action', {
    data:{state:{dashboard:{setup_action:'demo_prepare'}}, context:{}}
  });
  assert((await denied.json()).ok === false);
  assert(errors.length === 0, errors.join('\n'));
  assert(external.length === 0, external.join('\n'));
  console.log('Browser: real-account pilot onboarding, supplied admin email, health and simulator isolation passed');
  await browser.close();
})().catch(e => {console.error(e);process.exit(1)});
