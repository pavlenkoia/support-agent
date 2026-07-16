import test from 'node:test'
import assert from 'node:assert/strict'

import { requestJson } from '../src/api/viewer.js'

test('accepts an empty 201 response when saving a new viewer push subscription', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(null, { status: 201 })

  try {
    await assert.doesNotReject(() => requestJson('/api/viewer/push/subscriptions', { method: 'POST' }))
  } finally {
    globalThis.fetch = originalFetch
  }
})
