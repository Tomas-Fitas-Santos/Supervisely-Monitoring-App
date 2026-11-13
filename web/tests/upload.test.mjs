import test from 'node:test';
import assert from 'node:assert/strict';
import {validateDatasetSources, uploadFile, uploadRequest, uploadSources, uploadDirectorySources} from '../src/upload_helpers.ts';

test('100k file validation yields, counts metadata, and never reads contents', async()=>{
  let yielded=false; setTimeout(()=>{yielded=true;},0);
  const files=Array.from({length:100000},(_,i)=>({name:`${i}.jpg`,webkitRelativePath:`birds/species/video/${i}.jpg`,size:150000,slice(){throw Error('content read');}}));
  const result=await validateDatasetSources(files,'folder');
  assert.equal(yielded,true);assert.equal(result.total,15000000000);assert.equal(result.sources.size,100000);
});

test('selection validation reads each file size once and accepts a FileList-like source', async()=>{
  let reads=0;
  const files={length:2,0:{name:'a.jpg',webkitRelativePath:'birds/species/video/a.jpg',get size(){reads++;return 100;}},
    1:{name:'b.jpg',webkitRelativePath:'birds/species/video/b.jpg',get size(){reads++;return 200;}}};
  const updates=[];
  const result=await validateDatasetSources(files,'folder',undefined,(count,bytes)=>updates.push({count,bytes}));
  assert.equal(reads,2);assert.equal(result.total,300);
  assert.deepEqual(updates.at(-1),{count:2,bytes:300});
});

function directory(name,children){return {name,kind:'directory',async* values(){yield* children();}};}
function frame(name,getFile=async()=>({name,size:100})){return {name,kind:'file',getFile};}

test('50k-folder upload starts before enumeration finishes, with bounded metadata and uploads', async()=>{
  let discovered=0,read=0,uploaded=0,active=0,maximum=0,firstRead=null;
  const frames=directory('clip',function*(){
    for(let i=0;i<50000;i++)yield frame(`frame_${i}.jpg`,async()=>{
      read++;return {name:`frame_${i}.jpg`,size:100};
    });
  });
  const species=directory('species',function*(){yield frames;});
  const root=directory('birds',function*(){yield species;});
  await uploadDirectorySources(root,async(path)=>{
    assert.match(path,/^birds\/species\/clip\/frame_\d+\.jpg$/);
    if(firstRead===null)firstRead=read;
    active++;maximum=Math.max(maximum,active);
    await new Promise(resolve=>setImmediate(resolve));
    active--;uploaded++;
  },new AbortController().signal,(count,bytes)=>{discovered=count;assert.equal(bytes,count*100);});
  assert.ok(firstRead<=4,`first upload waited for ${firstRead} metadata reads`);
  assert.equal(uploaded,50000);assert.equal(discovered,50000);assert.equal(maximum,4);
});

test('directory read or upload failure settles active writes and stops scheduling new files', async()=>{
  let active=0,started=0;
  const root=directory('birds',function*(){
    yield directory('species',function*(){yield directory('clip',function*(){
      for(let i=0;i<20;i++)yield frame(`frame_${i}.jpg`);
    });});
  });
  await assert.rejects(uploadDirectorySources(root,async()=>{
    started++;active++;
    try{await new Promise(resolve=>setTimeout(resolve,5));throw Error('network stopped');}
    finally{active--;}
  },new AbortController().signal),/network stopped/);
  assert.equal(active,0);assert.equal(started,4);
});

test('pause does not wait for a stalled browser metadata read or send any late upload', async()=>{
  let uploads=0,reads=0;
  const controller=new AbortController();
  const root=directory('birds',function*(){yield directory('species',function*(){
    yield directory('clip',function*(){yield frame('frame_1.jpg',()=>{
      reads++;controller.abort();return new Promise(()=>{});
    });});
  });});
  await assert.rejects(uploadDirectorySources(root,async()=>{uploads++;},controller.signal),/paused/);
  assert.equal(reads,1);assert.equal(uploads,0);
});

test('invalid directory paths fail before upload and empty folders fail visibly', async()=>{
  let uploads=0;
  const unsafe=directory('birds',function*(){yield frame('../escape.jpg');});
  await assert.rejects(uploadDirectorySources(unsafe,async()=>{uploads++;},new AbortController().signal),/Invalid source filename/);
  const empty=directory('birds',function*(){});
  await assert.rejects(uploadDirectorySources(empty,async()=>{uploads++;},new AbortController().signal),/no supported files/);
  assert.equal(uploads,0);
});

test('folder requests use bounded concurrency and settle active writes before failing', async()=>{
  const sources=new Map(Array.from({length:20},(_,i)=>[String(i),{size:1}]));
  let active=0, maximum=0, started=0;
  await assert.rejects(uploadSources(sources,async(path)=>{
    active++;started++;maximum=Math.max(maximum,active);
    try{await new Promise(resolve=>setTimeout(resolve,5));if(path==='0')throw Error('network stopped');}
    finally{active--;}
  }),/network stopped/);
  assert.equal(active,0);assert.equal(maximum,4);assert.equal(started,4);
});
test('request timeout covers stalled response bodies', async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>({ok:true,status:200,json:()=>new Promise(()=>{})});
  try {await assert.rejects(uploadRequest('/stall',{},undefined,15),/timed out/);}
  finally {globalThis.fetch=original;}
});
test('abort releases a stalled request promptly', async()=>{
  const original=globalThis.fetch;globalThis.fetch=()=>new Promise(()=>{});
  const controller=new AbortController();
  try {const pending=uploadRequest('/stall',{},controller.signal,5000);controller.abort();await assert.rejects(pending,/paused/);}
  finally {globalThis.fetch=original;}
});
test('lost response reconciles committed offset without duplicate bytes; chunks bounded', async()=>{
  const original=globalThis.fetch;let committed=0;let calls=0;const sizes=[];
  globalThis.fetch=async(_url,options)=>{calls++;sizes.push(options.body.size);committed+=options.body.size;
    if(calls===1)throw Error('lost reply');return {ok:true,status:200,json:async()=>({received_bytes:committed})};};
  try {let progress=0;await uploadFile({file:new Blob([new Uint8Array(20*1024*1024)]),offset:0,url:'/chunk',csrf:'token',signal:new AbortController().signal,readOffset:async()=>committed,onProgress:n=>{progress+=n;}});
    assert.equal(progress,20*1024*1024);assert.equal(calls,3);assert.ok(sizes.every(n=>n<=8*1024*1024));}
  finally {globalThis.fetch=original;}
});

test('ZIP larger than 5 GiB uses bounded slices and preserves 64-bit resume offsets', async()=>{
  const original=globalThis.fetch;
  const size=6*1024**3+123, start=5*1024**3+57;
  let committed=start, progress=0, largest=0;
  // A virtual Blob exercises all offsets without allocating gigabytes of RAM.
  const file={size,slice(a,b){assert.equal(a,committed);largest=Math.max(largest,b-a);return {size:b-a};}};
  globalThis.fetch=async(url,options)=>{
    assert.equal(Number(new URL('http://test'+url).searchParams.get('offset')),committed);
    committed+=options.body.size;
    return {ok:true,status:200,json:async()=>({received_bytes:committed})};
  };
  try{
    await uploadFile({file,offset:start,url:'/chunk?file=zip',csrf:'token',signal:new AbortController().signal,
      readOffset:async()=>committed,onProgress:n=>{progress+=n;}});
    assert.equal(committed,size);assert.equal(progress,size-start);assert.ok(largest<=8*1024*1024);
  }finally{globalThis.fetch=original;}
});
