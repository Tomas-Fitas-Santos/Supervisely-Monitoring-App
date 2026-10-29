const {chromium}=require('playwright');
const assert=require('assert');
const path=require('path');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto('http://127.0.0.1:8000',{waitUntil:'networkidle'});
  await page.getByRole('button',{name:'Load bundled demo CSV',exact:true}).click();
  const rows=page.locator('.nj-registration');await rows.nth(1).waitFor();
  for(let i=0;i<2;i++){
    await rows.nth(i).getByRole('button',{name:'Create team and send invitations',exact:true}).click();
    await rows.nth(i).getByRole('button',{name:'Simulate invitation acceptance',exact:true}).click();
    await rows.nth(i).getByText('Ready',{exact:true}).waitFor();
  }
  await page.getByRole('tab',{name:'Dataset',exact:true}).click();
  const upload=page.locator('.nj-panel').filter({has:page.getByRole('heading',{name:'Upload dataset',exact:true})});
  await upload.locator('select').first().selectOption('30');
  await upload.getByLabel('Project name',{exact:true}).fill('Browser folder upload');
  await upload.locator('input[webkitdirectory]').setInputFiles(path.join(__dirname,'../examples/organiser-demo/dataset'));
  await upload.getByText('2 media files selected.',{exact:true}).waitFor();
  await upload.getByRole('button',{name:'Upload to Supervisely',exact:true}).click();
  await upload.getByText('Dataset uploaded. Select it for distribution below.',{exact:true}).waitFor();
  await page.locator('summary').filter({hasText:'Event rules and scoring'}).click();
  await page.getByRole('button',{name:'Save and freeze official rules',exact:true}).click();
  await page.getByText('Rules are frozen.',{exact:false}).waitFor();
  const dist=page.locator('.nj-panel').filter({has:page.getByRole('heading',{name:'Distribute dataset to teams',exact:true})});
  const checks=dist.locator('input[type=checkbox]');
  for(let i=0;i<await checks.count();i++){
    const checkbox=checks.nth(i);const text=await checkbox.locator('..').innerText();
    if(!text.includes('Copy existing'))await checkbox.check();
  }
  await dist.getByLabel('Independent teams per source',{exact:true}).fill('1');
  await dist.locator('summary').filter({hasText:'Per-source effort overrides and inventory'}).click();
  await dist.getByRole('button',{name:'Inspect selected inventory',exact:true}).click();
  await dist.getByText('garden/bird.jpg',{exact:false}).waitFor();
  await dist.getByText('sparrows/bird.jpg',{exact:false}).waitFor();
  await dist.getByRole('button',{name:'Preview distribution',exact:true}).click();
  const preview=page.locator('.nj-panel').filter({has:page.getByRole('heading',{name:'Review dataset distribution',exact:true})});
  await preview.getByText('workload spread 0',{exact:false}).waitFor();
  const allocations=preview.locator('tbody tr');assert(await allocations.count()===2);
  for(let i=0;i<2;i++){
    assert(await allocations.nth(i).locator('td').nth(2).innerText()==='1');
    assert(await allocations.nth(i).locator('td').nth(3).innerText()==='0');
  }
  await preview.getByRole('button',{name:'Apply distribution',exact:true}).click();
  await page.getByText('Batches prepared',{exact:true}).first().waitFor();
  await page.getByRole('tab',{name:'Monitoring',exact:true}).click();
  await page.getByRole('button',{name:'Release this batch',exact:true}).click();
  await page.getByRole('button',{name:'Simulate participant submission',exact:true}).click();
  await page.locator('.nj-unit').first().waitFor();
  assert(errors.length===0,errors.join('\n'));
  console.log('Browser: real folder upload, duplicate basenames, even non-overlapping image distribution and task release passed');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
