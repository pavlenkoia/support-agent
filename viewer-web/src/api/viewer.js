const API_PREFIX = '/api/viewer'

async function requestJson(path) {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`)
  }
  return response.json()
}

export function fetchDialogs(day) {
  return requestJson(`${API_PREFIX}/dialogs?day=${encodeURIComponent(day)}`)
}

export function fetchDialogMessages(conversationId, day) {
  return requestJson(`${API_PREFIX}/dialogs/${encodeURIComponent(conversationId)}/messages?day=${encodeURIComponent(day)}`)
}
