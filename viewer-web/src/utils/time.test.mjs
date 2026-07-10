import test from 'node:test'
import assert from 'node:assert/strict'

import { formatDateInputValue } from './time.js'

test('formatDateInputValue uses local business day instead of UTC day near midnight', () => {
  const value = '2026-07-09T19:43:05.456Z'

  assert.equal(formatDateInputValue(value, 'UTC'), '2026-07-09')
  assert.equal(formatDateInputValue(value, 'Asia/Yekaterinburg'), '2026-07-10')
})
