import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source=await readFile(new URL('../src/annotation_marker_size.ts',import.meta.url),'utf8');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {keypointSize}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
test('unzoomed normal and low-resolution images keep their existing appearance',()=>{
  assert.deepEqual(keypointSize(1280,720),{radius:2.88,strokeWidth:0.576});
  assert.deepEqual(keypointSize(160,120),{radius:1,strokeWidth:0.35});
});

for(const zoom of [1.25,2,3]){
  test(`dots and outlines stay the same screen size at ${zoom}x zoom`,()=>{
    for(const [width,height] of [[160,120],[1280,720],[1920,1080]]){
      const normal=keypointSize(width,height);
      const magnified=keypointSize(width,height,zoom);
      assert.ok(Math.abs(magnified.radius*zoom-normal.radius)<1e-10);
      assert.ok(Math.abs(magnified.strokeWidth*zoom-normal.strokeWidth)<1e-10);
    }
  });
}

test('invalid or sub-100% magnification keeps a finite default',()=>{
  for(const zoom of [0,-1,NaN,Infinity]){
    assert.deepEqual(keypointSize(160,120,zoom),keypointSize(160,120));
  }
});
