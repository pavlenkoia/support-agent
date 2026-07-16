export function base64UrlToUint8Array(value) {
  const normalized = `${value}`.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), '=')
  const binary = atob(padded)
  return Uint8Array.from(binary, (character) => character.charCodeAt(0))
}

export function notificationClickTarget({ conversation_id: conversationId, case_id: caseId } = {}) {
  const params = new URLSearchParams()
  if (conversationId) params.set('conversation_id', conversationId)
  if (caseId) params.set('case_id', `${caseId}`)
  const query = params.toString()
  return query ? `/?${query}` : '/'
}
