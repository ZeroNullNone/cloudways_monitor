// Run via the Codex Browser runtime: await runAcceptance(browser, tab, outputDir).
// The target must be the fixture server from scripts/e2e_preview.py.
import { mkdir, writeFile } from 'node:fs/promises';

export async function runAcceptance(browser, tab, outputDir) {
  await mkdir(outputDir, {recursive:true});
  const results=[];
  const check=(name,pass)=>{ results.push({name,pass:Boolean(pass)}); if(!pass) throw new Error(`Acceptance failed: ${name}`); };
  const viewport=await browser.capabilities.get('viewport');
  const s1=tab.playwright.getByRole('region',{name:'Server 1',exact:true});
  const s2=tab.playwright.getByRole('region',{name:'Server 2',exact:true});
  try {
    await viewport.set({width:1280,height:900});
    check('All servers initially expanded', (await tab.playwright.getByRole('button',{name:/^Server [12] DigitalOcean/}).all()).length===2 && await tab.playwright.getByRole('button',{name:/^Server 1 DigitalOcean/}).getAttribute('aria-expanded')==='true' && await tab.playwright.getByRole('button',{name:/^Server 2 DigitalOcean/}).getAttribute('aria-expanded')==='true');
    check('All 15 DigitalOcean targets available', await tab.playwright.getByLabel('Metric for Server 1',{exact:true}).locator('option').count()===15);
    check('All six durations available', await tab.playwright.getByLabel('Duration for Server 1',{exact:true}).locator('option').count()===6);
    check('Trailing null excluded; completed summary shown', await s1.locator('.graph-summary .summary-latest dt').innerText()==='Latest completed');
    const hierarchy=await s1.locator('.graph-summary').evaluate(e=>{
      const latest=e.querySelector('.summary-latest dd');
      const secondary=e.querySelector('.summary-secondary dd');
      return latest && secondary ? {latest:getComputedStyle(latest).fontSize,secondary:getComputedStyle(secondary).fontSize} : null;
    });
    check('Latest has a clear visual priority',hierarchy && Number.parseFloat(hierarchy.latest)>=Number.parseFloat(hierarchy.secondary)*1.8);
    check('Sample table and coverage footnotes removed',await s1.getByText('Show samples',{exact:true}).count()===0 && await s1.getByText(/^Returned coverage:/).count()===0 && await s1.getByText(/valid samples/).count()===0);
    check('Completed free-memory sample shown', await s1.locator('.server-metrics').getByText('Free memory',{exact:true}).count()===1);
    check('Capacity shown as smaller gray text beside free metrics',await s1.locator('.server-metrics .metric-capacity').count()===2 && (await s1.locator('.server-metrics').innerText()).includes('Total 2 GB') && (await s1.locator('.server-metrics').innerText()).includes('Total 100 GB'));
    const capacityStyle=await s1.locator('.metric-capacity').first().evaluate(e=>({color:getComputedStyle(e).color,size:getComputedStyle(e).fontSize,parentSize:getComputedStyle(e.parentElement).fontSize,muted:getComputedStyle(document.documentElement).getPropertyValue('--muted').trim()}));
    check('Total capacity remains secondary and neutral',Number.parseFloat(capacityStyle.size)<Number.parseFloat(capacityStyle.parentSize) && capacityStyle.color!=='rgb(255, 255, 255)');
    check('Actual zero retained', await s2.getByText('0 GB',{exact:true}).count()===1);
    const geometry=await tab.playwright.evaluate(()=>{const pane=document.querySelector('.server-workspace'); return {width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,columns:getComputedStyle(pane).gridTemplateColumns};});
    check('Desktop has two columns and no page overflow', geometry.width>=1200 && geometry.scrollWidth<=geometry.width && geometry.columns.split(' ').length===2);
    await tab.playwright.getByLabel('Metric for Server 1',{exact:true}).selectOption('Reads per second');
    await s1.getByRole('img',{name:/Reads per second time series/}).waitFor({state:'visible'});
    check('Metric switch updates chart',await s1.getByRole('img',{name:/Reads per second time series/}).count()===1);
    await tab.playwright.getByLabel('Duration for Server 1',{exact:true}).selectOption('6 Months');
    await s1.getByRole('img',{name:/Reads per second time series.*6 Months/}).waitFor({state:'visible'});
    check('Long duration updates chart',await s1.getByRole('img',{name:/Reads per second time series.*6 Months/}).count()===1);
    await tab.playwright.getByRole('button',{name:'Collapse all',exact:true}).click();
    check('Collapse all hides graph controls',!(await tab.playwright.getByLabel('Metric for Server 1',{exact:true}).isVisible()));
    await tab.playwright.getByRole('button',{name:'Expand all',exact:true}).click();
    check('Selections survive collapse', await tab.playwright.getByLabel('Metric for Server 1',{exact:true}).evaluate(e=>e.value)==='Reads per second');
    await tab.playwright.getByRole('searchbox',{name:'Search servers and applications'}).fill('app3.example.com');
    check('Application search retains parent and filters siblings',await s1.isVisible() && !(await s2.isVisible()) && await s1.getByText('App 3',{exact:true}).isVisible() && !(await s1.getByText('App 1',{exact:true}).isVisible()));
    await tab.playwright.getByRole('searchbox',{name:'Search servers and applications'}).fill('');
    await tab.playwright.getByLabel('Sort applications for Server 1',{exact:true}).selectOption('traffic_requests');
    check('Returned request sorting orders biggest application first',await s1.locator('.application-table tbody tr').first().innerText().then(text=>text.includes('App 3')));
    check('Disk usage shown for all applications', await s1.getByText('12.4 GB',{exact:true}).count()===1 && await s1.getByText('2.1 GB',{exact:true}).count()===1);
    check('Partial requests and unavailable bandwidth are labelled',await s1.getByText('Partial',{exact:true}).count()===2 && await s1.getByText('Unavailable',{exact:true}).count()===2);
    await s1.locator('summary').filter({hasText:'App 1'}).click();
    const analytics=s1.getByRole('region',{name:'Analytics for App 1',exact:true});
    await analytics.getByText('Returned requests',{exact:true}).waitFor({state:'visible'});
    check('Application expands into inline status analysis',await analytics.getByText('Server errors (5xx)',{exact:true}).count()===1 && await analytics.getByText('Partial results',{exact:true}).count()===1);
    await analytics.getByLabel('Analysis for App 1',{exact:true}).selectOption('urls');
    await analytics.getByText('/checkout',{exact:true}).waitFor({state:'visible'});
    check('Top URLs load inside application details',await analytics.getByText('/checkout',{exact:true}).count()===1);
    await analytics.getByLabel('Analysis for App 1',{exact:true}).selectOption('php');
    await analytics.getByText('No slow pages returned for this period.',{exact:true}).waitFor({state:'visible'});
    check('Genuine empty slow-page result is clear',await analytics.getByText('Returned slow pages',{exact:true}).count()===1);
    await analytics.getByLabel('Analysis for App 1',{exact:true}).selectOption('mysql');
    await analytics.getByText('SELECT demo',{exact:true}).waitFor({state:'visible'});
    check('Slow queries are available inline',await analytics.getByText('SELECT demo',{exact:true}).count()===1);
    await analytics.getByLabel('Analysis duration for App 1',{exact:true}).selectOption('1d');
    await analytics.locator('.analytics-result-meta').getByText('Last 1 day',{exact:true}).waitFor({state:'visible'});
    check('Application analysis duration switches independently',await analytics.locator('.analytics-result-meta').getByText('Last 1 day',{exact:true}).count()===1);
    await tab.playwright.getByRole('button',{name:'Collapse all',exact:true}).click();
    check('Collapsing server removes application polling component',await s1.locator('.application-analytics').count()===0);
    await tab.playwright.getByRole('button',{name:'Expand all',exact:true}).click();
    await analytics.getByText('Returned requests',{exact:true}).waitFor({state:'visible'});
    await tab.playwright.getByLabel('Sort applications for Server 1',{exact:true}).selectOption('disk_used_gb');
    await tab.playwright.getByLabel('Metric for Server 1',{exact:true}).selectOption('Idle CPU');
    await tab.playwright.getByLabel('Duration for Server 1',{exact:true}).selectOption('1 Hour');
    await s1.getByRole('img',{name:/Idle CPU time series/}).waitFor({state:'visible'});
    await tab.dom_cua.scroll({x:0,y:-9999});
    await writeFile(`${outputDir}/desktop.png`,await tab.screenshot({fullPage:false}));
    await writeFile(`${outputDir}/desktop-full.png`,await tab.screenshot({fullPage:true}));
    await writeFile(`${outputDir}/desktop-dom.txt`,await tab.playwright.domSnapshot());
    await viewport.set({width:390,height:844});
    const mobile=await tab.playwright.evaluate(()=>({width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,columns:getComputedStyle(document.querySelector('.server-workspace')).gridTemplateColumns}));
    check('Mobile stacks panes without page overflow',mobile.width>=360 && mobile.width<=390 && mobile.scrollWidth<=mobile.width && mobile.columns.split(' ').length===1);
    check('Expanded application analysis has no mobile overflow',await analytics.isVisible() && mobile.scrollWidth<=mobile.width);
    await writeFile(`${outputDir}/mobile.png`,await tab.screenshot({fullPage:true}));
    check('English UI and UTC+8', (await tab.playwright.domSnapshot()).includes('All times UTC+8') && !(await tab.playwright.evaluate(()=>document.body.innerText)).match(/[\u3400-\u9fff]/));
    const errors=await tab.dev.logs({levels:['error'],limit:20});
    check('No browser console errors',errors.length===0);
    await writeFile(`${outputDir}/browser-results.json`,JSON.stringify({fixtureOnly:true,results},null,2));
    return results;
  } finally {
    await writeFile(`${outputDir}/browser-results.json`,JSON.stringify({fixtureOnly:true,results},null,2));
    await viewport.reset();
  }
}
