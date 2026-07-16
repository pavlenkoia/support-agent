import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('keeps a high-visibility native notification while the viewer is in background', async () => {
  const source = await readFile(new URL('../src/sw.js', import.meta.url), 'utf8')

  assert.match(source, /requireInteraction:\s*true/)
  assert.match(source, /vibrate:\s*\[200,\s*100,\s*200\]/)
  assert.match(source, /visibilityState === 'visible'/)
})
