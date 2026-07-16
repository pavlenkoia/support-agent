import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('always shows a high-visibility native notification for an inbound viewer push', async () => {
  const source = await readFile(new URL('../src/sw.js', import.meta.url), 'utf8')

  assert.match(source, /requireInteraction:\s*true/)
  assert.match(source, /vibrate:\s*\[200,\s*100,\s*200\]/)
  assert.match(source, /self\.skipWaiting\(\)/)
  assert.match(source, /self\.clients\.claim\(\)/)
  assert.match(source, /windows\.forEach\(\(client\) => client\.postMessage/)
  assert.doesNotMatch(source, /if \(visibleWindow\) \{[\s\S]*?return/)
})
