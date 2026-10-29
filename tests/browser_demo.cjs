const {chromium}=require('playwright');const assert=require('assert');const path=require('path');
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1100}});const errors=[],external=[];
page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:8000')&&!r.url().startsWith('data:'))external.push(r.url())});
await page.goto('http://127.0.0.1:8000',{waitUntil:'networkidle'});await page.getByText('Offline simulator — no real Supervisely resources').waitFor();
await page.getByRole('button',{name:'Load bundled demo CSV',exact:true}).click();
const registrations=page.locator('.nj-registration');await registrations.nth(1).waitFor();assert(await registrations.count()===2);
await registrations.nth(0).getByText('tomas.gaming.21@gmail.com',{exact:false}).waitFor();await registrations.nth(1).getByText('tomas.santos.work2002@gmail.com',{exact:false}).waitFor();
for(let i=0;i<2;i++){
  const row=registrations.nth(i);await row.getByRole('button',{name:'Create team and send invitations',exact:true}).click();
  await row.getByText('Pending invitations',{exact:true}).first().waitFor();
}
const createdRows=page.locator('table tbody tr');assert((await createdRows.allTextContents()).filter(t=>t.includes('Pending invitations')).length===2);
for(let i=0;i<2;i++){
  const row=registrations.nth(i);await row.getByRole('button',{name:'Simulate invitation acceptance',exact:true}).click();
  await row.getByText('Ready',{exact:true}).waitFor();
}
console.log('Browser: two email teams visible while Pending, then accepted with correct membership roles');
await page.getByRole('tab',{name:'Dataset',exact:true}).click();await page.getByRole('button',{name:'Load bundled CC0 bird photos',exact:true}).click();
await page.locator('summary').filter({hasText:'Event rules and scoring'}).click();await page.getByRole('button',{name:'Save and freeze official rules',exact:true}).click();await page.getByText('Rules are frozen.',{exact:false}).waitFor();
const dist=page.locator('.nj-panel').filter({has:page.getByRole('heading',{name:'Distribute dataset to teams',exact:true})});
const checks=dist.locator('input[type=checkbox]');for(let i=0;i<await checks.count();i++){const checkbox=checks.nth(i);const text=await checkbox.locator('..').innerText();if(!text.includes('Copy existing'))await checkbox.check();}
await dist.getByRole('button',{name:'Preview distribution',exact:true}).click();await page.getByRole('button',{name:'Apply distribution',exact:true}).click();await page.getByText('Batches prepared',{exact:true}).first().waitFor();console.log('Browser: frozen rules, nested photo inventory and distribution passed');
await page.getByRole('tab',{name:'Monitoring',exact:true}).click();await page.getByRole('button',{name:'Release this batch',exact:true}).click();await page.getByRole('button',{name:'Simulate participant submission',exact:true}).click();
await page.locator('.nj-unit').first().waitFor();await page.locator('.nj-unit').first().click();await page.getByRole('button',{name:'Load this captured image / frame',exact:true}).click();await page.locator('.nj-competition-review img').waitFor();await page.locator('.nj-competition-review').getByRole('button',{name:'good',exact:true}).click();await page.getByText('Saved: good',{exact:false}).waitFor();
const rows=await page.locator('table tbody tr').allTextContents();assert(rows.some(r=>r.includes('1 / 0 / 0')));console.log('Browser: real photo preview, captured submission and scoring passed');
await page.screenshot({path:path.join(process.env.DEMO_SCREENSHOT_DIR || require('os').tmpdir(),'monitor-competition.png'),fullPage:true});
await page.getByRole('combobox',{name:'Demo role',exact:true}).selectOption('901');await page.getByRole('checkbox',{name:'Organiser oversight: inspect all teams and adjudicate judgments'}).waitFor({state:'detached'});assert(await page.getByRole('button',{name:'Close event: stop jobs and capture final state',exact:true}).count()===0);
await page.getByRole('combobox',{name:'Demo role',exact:true}).selectOption('900');await page.getByRole('checkbox',{name:'Organiser oversight: inspect all teams and adjudicate judgments'}).check();
await page.getByRole('button',{name:'Close event: stop jobs and capture final state',exact:true}).click();await page.getByText('Event closed: current native jobs have been stopped.',{exact:false}).waitFor();
const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download complete event records',exact:true}).click();const file=await download;assert(file.suggestedFilename()==='event-annotation-records.json');
console.log('Browser: monitor isolation, closure and export passed');
await page.getByRole('button',{name:'Reset this demo (previous results are backed up)',exact:true}).click();
const prepared=page.waitForResponse(r=>r.url().endsWith('/setup/action')&&r.request().postDataJSON()?.state?.dashboard?.setup_action==='demo_prepare');
await page.getByRole('button',{name:'Prepare working presentation',exact:true}).click();
assert((await (await prepared).json()).ok===true,await page.locator('body').innerText());
await page.getByRole('button',{name:'Prepare working presentation',exact:true}).waitFor({state:'detached'});
await page.getByRole('tab',{name:'Monitoring',exact:true}).click();
await page.locator('.nj-unit').first().waitFor();
await page.locator('.nj-unit').first().click();
await page.getByRole('button',{name:'Load this captured image / frame',exact:true}).click();
await page.locator('.nj-competition-review img').waitFor();
assert(errors.length===0,errors.join('\n'));assert(external.length===0,external.join('\n'));console.log('Browser: one-click presentation with captured photo passed; zero external browser requests or JavaScript errors');await browser.close();})().catch(e=>{console.error(e);process.exit(1)});
