import test from 'node:test'
import assert from 'node:assert/strict'

import { getHorizontalDaySwipeDirection, shiftDateInputDay } from '../src/utils/day-navigation.js'

test('shifts a date input day across month, year, and leap-day boundaries', () => {
  assert.equal(shiftDateInputDay('2026-03-01', -1), '2026-02-28')
  assert.equal(shiftDateInputDay('2026-01-01', -1), '2025-12-31')
  assert.equal(shiftDateInputDay('2024-02-28', 1), '2024-02-29')
  assert.equal(shiftDateInputDay('2024-02-29', 1), '2024-03-01')
})

test('maps clear horizontal list swipes to adjacent calendar days', () => {
  assert.equal(
    getHorizontalDaySwipeDirection({ startX: 20, startY: 100, endX: 108, endY: 106 }),
    'previous',
  )
  assert.equal(
    getHorizontalDaySwipeDirection({ startX: 230, startY: 100, endX: 142, endY: 104 }),
    'next',
  )
})

test('ignores short, vertical, and strongly diagonal gestures', () => {
  assert.equal(
    getHorizontalDaySwipeDirection({ startX: 20, startY: 100, endX: 65, endY: 102 }),
    null,
  )
  assert.equal(
    getHorizontalDaySwipeDirection({ startX: 20, startY: 100, endX: 26, endY: 198 }),
    null,
  )
  assert.equal(
    getHorizontalDaySwipeDirection({ startX: 20, startY: 100, endX: 96, endY: 180 }),
    null,
  )
})
