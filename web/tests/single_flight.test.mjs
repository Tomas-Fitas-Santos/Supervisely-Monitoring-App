import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/single_flight.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}}).outputText;
const { singleFlight } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));

test('slow refreshes share one request, then allow another after completion', async () => {
  let calls = 0, finish;
  const refresh = singleFlight(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  const first = refresh();
  const second = refresh();
  assert.equal(first, second);
  await Promise.resolve();
  assert.equal(calls, 1);
  finish('status');
  assert.equal(await first, 'status');
  const third = refresh();
  await Promise.resolve();
  assert.equal(calls, 2);
  finish('updated');
  assert.equal(await third, 'updated');
});

test('a failed refresh releases the gate for retries', async () => {
  let calls = 0;
  const refresh = singleFlight(async () => { if (++calls === 1) throw new Error('offline'); return 'online'; });
  await assert.rejects(refresh(), /offline/);
  assert.equal(await refresh(), 'online');
});
