export function selectConversationAfterDialogsReload({
  dialogs,
  currentConversationId,
  requestedConversationId,
}) {
  const hasConversation = (conversationId) => dialogs.some((dialog) => dialog.conversation_id === conversationId)

  if (hasConversation(requestedConversationId)) return requestedConversationId
  if (hasConversation(currentConversationId)) return currentConversationId

  return dialogs[0]?.conversation_id ?? null
}
