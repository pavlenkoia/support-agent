import test from 'node:test'
import assert from 'node:assert/strict'

import { selectConversationAfterDialogsReload } from '../src/utils/dialog-selection.js'

const dialogs = [
  { conversation_id: 'first' },
  { conversation_id: 'selected' },
  { conversation_id: 'third' },
]

test('keeps the currently selected dialog after a manual refresh when it is still present', () => {
  assert.equal(
    selectConversationAfterDialogsReload({
      dialogs,
      currentConversationId: 'selected',
      requestedConversationId: null,
    }),
    'selected',
  )
})

test('prioritizes a notification-requested dialog and falls back to the first dialog only when necessary', () => {
  assert.equal(
    selectConversationAfterDialogsReload({
      dialogs,
      currentConversationId: 'selected',
      requestedConversationId: 'third',
    }),
    'third',
  )
  assert.equal(
    selectConversationAfterDialogsReload({
      dialogs,
      currentConversationId: 'missing',
      requestedConversationId: null,
    }),
    'first',
  )
  assert.equal(
    selectConversationAfterDialogsReload({
      dialogs: [],
      currentConversationId: 'selected',
      requestedConversationId: null,
    }),
    null,
  )
})
