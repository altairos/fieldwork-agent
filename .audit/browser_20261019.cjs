const {chromium} = require('../web/node_modules/@playwright/test');
(async () => {
  const browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page = await context.newPage();
  const errors=[];
  const responses=[];
  page.on('response', r=>{if(r.request().method()==='POST')responses.push({url:new URL(r.url()).pathname,status:r.status()});});
  page.on('pageerror', e=>errors.push(e.message));
  // Provider traffic is substituted at the Python client boundary. Block unrelated remote requests.
  await context.route('**/*', route => route.request().url().startsWith('http://127.0.0.1:') ? route.continue() : route.abort());
  try {
    await page.goto(`http://127.0.0.1:${process.argv[2]}/`);
    await page.locator('textarea').first().fill('AUDIT_SITE');
    await page.getByRole('button',{name:'创建项目',exact:true}).click();
    await page.waitForURL(/\/p\/[^/?]+/);
    await page.getByText('AUDIT_SITE 完成。',{exact:true}).waitFor({timeout:20000});
    if (await page.getByRole('button',{name:'稍后',exact:true}).count()) await page.getByRole('button',{name:'稍后',exact:true}).click();
    await page.getByRole('tab',{name:'田野点',exact:true}).click();
    const before = await page.getByRole('button',{name:'确认',exact:true}).count();
    await page.reload();
    await page.getByRole('tab',{name:'田野点',exact:true}).click();
    await page.getByRole('button',{name:'确认',exact:true}).waitFor({timeout:10000});
    const after = await page.getByRole('button',{name:'确认',exact:true}).count();
    await page.getByRole('button',{name:'确认',exact:true}).first().click();
    async function send(text) {
      await page.getByLabel('消息输入').fill(text);
      await page.getByLabel('消息输入').press('Enter');
      await page.getByText(text+' 完成。',{exact:true}).waitFor({timeout:20000});
    }
    await send('AUDIT_ITINERARY');
    await send('AUDIT_PROPOSAL');
    const projectUrl=page.url().split('?')[0];
    await page.goto(projectUrl+'/proposal');
    await page.getByRole('button',{name:/确认/}).last().waitFor({timeout:10000});
    const confirm = page.getByRole('button',{name:/确认/}).last();
    const confirmLabel=await confirm.innerText();
    await confirm.click();
    const downloadButton=page.getByRole('button',{name:/下载/});
    const download=await Promise.all([page.waitForEvent('download'),downloadButton.click()]).then(r=>r[0]);
    console.log(JSON.stringify({backend:process.argv[3],first_adopt_confirm_buttons:before,after_reload_confirm_buttons:after,
      confirmLabel,download:download.suggestedFilename(),errors}));
  } catch(err) {console.log(JSON.stringify({backend:process.argv[3],responses,body:(await page.locator('body').innerText()).slice(-6000)}));throw err;}
  finally {await browser.close();}
})().catch(err=>{console.error(err);process.exitCode=1;});
