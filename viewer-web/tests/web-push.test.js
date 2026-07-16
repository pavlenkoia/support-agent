import test from 'node:test'
import assert from 'node:assert/strict'

import { base64UrlToUint8Array, notificationClickTarget } from '../src/utils/web-push.js'

test('converts a VAPID base64url public key to the browser subscription byte array', () => {
  assert.deepEqual([...base64UrlToUint8Array('AQIDBA')], [1, 2, 3, 4])
})

test('builds a viewer target retaining the conversation and case from push data', () => {
  assert.equal(
    notificationClickTarget({ conversation_id: 'vk:123', case_id: 42 }),
    '/?conversation_id=vk%3A123&case_id=42',
  )
})
