import test from 'node:test';
import assert from 'node:assert/strict';
import {validateDatasetSources, uploadFile, uploadRequest} from '../src/upload_helpers.ts';

test('100k file validation yields, counts metadata, and never reads contents', async()=>{
  let yielded=false; setTimeout(()=>{yielded=true;},0);
  const files=Array.from({length:100000},(_,i)=>({name:`${i}.jpg`,webkitRelativePath:`birds/species/video/${i}.jpg`,size:150000,slice(){throw Error('content read');}}));
  const result=await validateDatasetSources(files,'folder');
  assert.equal(yielded,true);assert.equal(result.total,15000000000);assert.equal(result.sources.size,100000);
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
