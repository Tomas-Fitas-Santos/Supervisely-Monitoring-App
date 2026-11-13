import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';

// Bundle only the transfer module so tests exercise its real coordinator without React.
const bundle=await build({entryPoints:['src/dataset_transfer.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const transfer=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const directory=(name,children)=>({kind:'directory',name,async* values(){yield* children;}});
const file=(name,size)=>({kind:'file',name,async getFile(){return new File([new Uint8Array(size)],name);}});
function completed(){return new Promise(resolve=>{
  const unsubscribe=transfer.watchDatasetTransfer(state=>{
    if(state&&!state.running){queueMicrotask(()=>{unsubscribe();resolve(state);});}
  });
});}

test('resuming a progressive folder skips complete files and sends only missing bytes',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;transfer.clearDatasetTransfer('resume');});
  const requests=[];
  globalThis.fetch=async(url,options)=>{
    const parsed=new URL(url,'http://test');requests.push({url:parsed,method:options.method,bytes:options.body?.size});
    let result={};
    if(parsed.pathname.endsWith('/upload-status'))result={file_progress:[
      {relative_path:'birds/species/clip/a.jpg',received_bytes:10,expected_bytes:10},
      {relative_path:'birds/species/clip/b.jpg',received_bytes:4,expected_bytes:10},
    ]};
    if(options.method==='PUT')result={received_bytes:10};
    return Response.json(result);
  };
  const root=directory('birds',[directory('species',[directory('clip',[file('a.jpg',10),file('b.jpg',10),file('c.jpg',10)])])]);
  transfer.beginDatasetTransfer({datasetId:'resume',name:'Resume',kind:'folder',files:[],directory:root,csrf:'test',resume:true});
  const state=await completed();
  assert.equal(state.phase,'queued');assert.equal(state.done,30);assert.equal(state.files,3);
  const uploads=requests.filter(r=>r.method==='PUT');
  assert.equal(uploads.length,2);
  assert.deepEqual(uploads.map(r=>[r.url.searchParams.get('relative_path'),r.url.searchParams.get('offset'),r.bytes]).sort(),[
    ['birds/species/clip/b.jpg','4',6],['birds/species/clip/c.jpg','0',10],
  ]);
  assert.ok(requests.at(-1).url.search.includes('background=true'));
});

test('missing saved files stop finalization and preserve the server upload',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;transfer.clearDatasetTransfer('missing');});
  const requests=[];
  globalThis.fetch=async(url,options)=>{
    requests.push(url);
    return Response.json(url.includes('upload-status')?{file_progress:[
      {relative_path:'birds/species/clip/missing.jpg',received_bytes:10,expected_bytes:10},
    ]}:options.method==='PUT'?{received_bytes:10}:{});
  };
  const root=directory('birds',[directory('species',[directory('clip',[file('a.jpg',10)])])]);
  transfer.beginDatasetTransfer({datasetId:'missing',name:'Missing',kind:'folder',files:[],directory:root,csrf:'test',resume:true});
  const state=await completed();
  assert.equal(state.phase,'interrupted');assert.match(state.error,/missing files/);
  assert.ok(!requests.some(url=>url.includes('/finalize')));
  assert.ok(!requests.some(url=>url.includes('/delete')));
});
