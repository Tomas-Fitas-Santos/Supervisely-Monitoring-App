/* Run with NODE_PATH pointing to a temporary Vue 2 / jsdom installation. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<div id="admin"></div><div id="monitor"></div>', {url:'http://localhost'});
global.window = dom.window; global.document = dom.window.document; global.navigator = dom.window.navigator;
global.crypto = require('crypto').webcrypto; global.FileReader = dom.window.FileReader;
const Vue = require('vue/dist/vue.common.js');
global.Vue = Vue; Vue.config.productionTip = false; Vue.config.devtools = false;
const warnings = []; Vue.config.warnHandler = message => warnings.push(message);
for (const name of ['connection.js','setup.js','dashboard.js']) {
  eval(fs.readFileSync(path.join(__dirname, '..', 'monitoring', 'static', name), 'utf8'));
}
const people = [
  {id:11, login:'alice', name:'Alice'}, {id:12, login:'bob', name:'Bob'},
  {id:21, login:'carol', name:'Carol'}, {id:22, login:'dave', name:'Dave'},
  {id:31, login:'erin', name:'Erin'}, {id:32, login:'frank', name:'Frank'}
];
const monitors = [{id:90,login:'mona',name:'Mona'}, {id:91,login:'mike',name:'Mike'}];
const teams = [
  {id:101,name:'Team A',monitor_id:90,annotator_ids:[11,12],participants:people.slice(0,2),revision:0,can_assign:true,has_batches:true},
  {id:102,name:'Team B',monitor_id:0,annotator_ids:[21,22],participants:people.slice(2,4),revision:0,can_assign:true,has_batches:false},
  {id:103,name:'Team C',monitor_id:91,annotator_ids:[31,32],participants:people.slice(4),revision:0,can_assign:true,has_batches:false}
];
const batches = [
  {id:'current',position:2,state:'active',kind:'images',submitted:false,job_ids:[71],remote_status:'in_progress',completed:1,total:2,
    assets:[{entity_id:501,source_id:'image-a'},{entity_id:502,source_id:'image-b'}]},
  {id:'submitted',position:1,state:'active',kind:'video',submitted:true,job_ids:[72],remote_status:'on_review',completed:1,total:1,
    assets:[{entity_id:601,source_id:'video-a',frames:25}]},
  {id:'upcoming',position:3,state:'locked',kind:'images',submitted:false,job_ids:[],remote_status:'not_created',completed:null,total:null,
    assets:[{entity_id:503,source_id:'image-c'}]}
].map(b=>({...b,synced_at:1,sync_error:null,stale:false,activity:{},activity_at:null,open_flags:0,reviews:[]}));
const calls = [];
function mount(id, uid, admin) {
  return new Vue({data:{view:{snapshot:{teams:[]},connected:false,can_setup:false,message:'',error:false,links:[],directory:{},sources:{},catalog:{},setup:{},review_preview:null,
      connection:{local:false,connected:false,login:'organiser',server_address:'https://app.supervisely.com'}},form:{}},
    methods:{async post(route) {
      const f = structuredClone(this.form); calls.push({uid,route,form:f}); await Promise.resolve();
      if (route === '/setup/action') {
        if (f.setup_action === 'assign') { const t = teams.find(t=>t.id===f.setup_team_id); t.monitor_id=f.monitor_id;t.revision++; }
        if (f.setup_action === 'unassign') { const t = teams.find(t=>t.id===f.setup_team_id); t.monitor_id=0;t.revision++; }
        this.view.sources={datasets:[{id:50,name:'Birds',project:'Event source',kind:'images',items:7}],workspaces:[{id:4,name:'Event sources'}]};
        if (admin && f.setup_action === 'catalog') this.view.catalog={users:people.concat(monitors),remote_teams:[]};
      }
      Object.assign(this.view,{connected:true,can_setup:admin,directory:{ready:true,monitors:structuredClone(monitors),annotators:structuredClone(people),teams:structuredClone(teams)},
        setup:admin?{groups:{ready:true,monitoring_team_id:10,annotator_team_id:20,workspace_id:4,monitors:structuredClone(monitors),annotators:structuredClone(people)},operations:[],blocked_by:null}:{},
        snapshot:{user_id:uid,refreshed_at:1,teams:teams.filter(t=>t.monitor_id===uid).map(t=>({...structuredClone(t),batches:t.id===101?structuredClone(batches):[]}))},message:'',error:false});
      return {ok:true};
    }},template:'<nightjar-dashboard :view="view" :form="form" :post="post"/>'}).$mount(id);
}
async function settle() { for(let i=0;i<8;i++) await Vue.nextTick(); }
function management(dashboard) { return dashboard.$children.find(c=>c.$options.name==='nightjar-management' && c.section===dashboard.tab); }
(async()=>{
  const admin=mount('#admin',90,true), monitor=mount('#monitor',91,false);await settle();
  const d=admin.$children[0], other=monitor.$children[0];
  assert.deepStrictEqual(Array.from(admin.$el.querySelectorAll('[role="tab"]')).map(b=>b.textContent),['Monitors & Teams','Dataset','Monitoring']);
  assert(calls.some(c=>c.uid===90 && c.form.setup_action==='catalog'),'Initial catalogue must load after parent refresh unlocks.');
  let roster=management(d);assert(roster);assert(admin.$el.textContent.includes('All participant teams'));
  roster.chooseMonitor(monitors[0]);await settle();
  assert(roster.assignedTeams.map(t=>t.name).join()==='Team A');
  assert(admin.$el.textContent.includes('Remove team from monitor'));
  roster.addTeamToMonitor=102;await roster.addToMonitor();await settle();
  assert(roster.assignedTeams.length===2);
  const assigned=roster.roster.find(t=>t.id===102);await roster.removeFromMonitor(assigned);await settle();
  assert(roster.roster.find(t=>t.id===102).monitor_id===0);
  roster.showAddTeam=true;roster.teamName='Pair D';roster.login1='grace';roster.login2='henry';roster.monitorId=null;
  await roster.createTeam();assert(calls.some(c=>c.form.setup_action==='register_team' && c.form.logins.join()==='grace,henry' && c.form.monitor_id===null));
  assert(roster.login1==='' && !roster.showAddTeam);
  await d.changeTab('dataset');await settle();const dataset=management(d);assert(dataset);
  assert(admin.$el.textContent.includes('Event source datasets'));assert(!admin.$el.textContent.includes('All participant teams'));
  assert(dataset.workspaceId===4);
  dataset.files=[new dom.window.File(['media'],'Bird.PNG',{type:'image/png'})];dataset.projectName='Sources';await dataset.upload();
  const chunk=calls.find(c=>c.form.setup_action==='chunk');assert(Buffer.from(chunk.form.chunk,'base64').toString()==='media');assert(!('chunk' in admin.form));
  dataset.selectedDatasets=[50];dataset.selectedTeams=[101];dataset.imageUnits=1;dataset.batchUnits=2;await dataset.plan();
  assert(calls.some(c=>c.form.setup_action==='preview' && c.form.dataset_ids[0]===50));
  await d.changeTab('roster');await settle();assert(management(d)===roster,'Roster selections must survive tab switches.');assert(roster.selectedMonitor===90);
  await d.changeTab('monitoring');await settle();
  assert(d.workingBatches.length===1 && d.submittedBatches.length===1 && d.upcomingBatches.length===1);
  assert(admin.$el.textContent.includes('Current batches'));assert(admin.$el.textContent.includes('Submitted batches'));assert(admin.$el.textContent.includes('Upcoming batches'));
  d.chooseBatch(d.team.batches.find(b=>b.id==='submitted'));d.frame=3;await d.send('preview_media');
  assert(calls.some(c=>c.form.action==='preview_media' && c.form.frame_index===3 && c.form.entity_id===601));
  admin.view.review_preview={team_id:101,batch_id:'submitted',entity_id:601,frame_index:3,original:'raw',annotated:'overlay',labels:1,classes:[],loaded_at:1};await settle();
  assert(d.mediaPreview && admin.$el.querySelector('.nj-media-preview').getAttribute('src')==='overlay');
  d.frame=4;await settle();assert(d.mediaPreview===null,'An old frame preview must not appear on a newly selected frame.');
  d.note='';await d.reviewAs('accepted');assert(calls.some(c=>c.form.action==='review' && c.form.decision==='accepted' && c.form.frame_index===4));
  await other.changeTab('roster');await settle();assert(monitor.$el.textContent.includes('All participant teams'));
  assert(!monitor.$el.textContent.includes('Save team') && !monitor.$el.textContent.includes('Remove team from monitor'));
  assert(other.teams.every(t=>t.monitor_id===91),'Monitoring must show only assigned teams.');
  assert(roster.selectedMonitor===90 && management(other).selectedMonitor===null,'Browsers must retain independent selections.');
  assert(warnings.length===0,warnings.join('\n'));
  admin.$destroy();monitor.$destroy();
  console.log('Three-tab UI: directory, assignments, participant entry, upload/distribution, batch groups, frame review and browser isolation passed.');
})().catch(error=>{console.error(error);process.exit(1)});
