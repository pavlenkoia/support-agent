import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('shows a foreground in-app alert when the service worker receives a user-message push', async () => {
  const [app, viewer] = await Promise.all([
    readFile(new URL('../src/App.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/pages/ViewerPage.jsx', import.meta.url), 'utf8'),
  ])

  assert.match(app, /setPushAlertToken\(\(current\) => current \+ 1\)/)
  assert.match(viewer, /🔔 Новое сообщение в VK — viewer обновлён/)
  assert.match(viewer, /navigator\.vibrate\?\.\(\[200, 100, 200\]\)/)
})
