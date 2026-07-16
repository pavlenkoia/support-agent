export class ViewerAuthError extends Error {
  constructor(message = 'Viewer authentication required.') {
    super(message)
    this.name = 'ViewerAuthError'
    this.status = 401
  }
}

const API_PREFIX = '/api/viewer'

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  })

  if (response.status === 401) {
    throw new ViewerAuthError()
  }

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`)
  }

  if (response.status === 204) {
    return null
  }

  return response.json()
}

export function fetchViewerAuthStatus() {
  return requestJson(`${API_PREFIX}/auth/me`)
}

export function loginToViewer(key) {
  return requestJson(`${API_PREFIX}/auth/login`, {
    method: 'POST',
    body: JSON.stringify({ key }),
  })
}

export function fetchDialogs(day) {
  return requestJson(`${API_PREFIX}/dialogs?day=${encodeURIComponent(day)}`)
}

export function fetchDialogMessages(conversationId, day) {
  return requestJson(`${API_PREFIX}/dialogs/${encodeURIComponent(conversationId)}/messages?day=${encodeURIComponent(day)}`)
}

export function fetchViewerPushConfig() {
  return requestJson(`${API_PREFIX}/push/config`)
}

export function saveViewerPushSubscription(subscription) {
  return requestJson(`${API_PREFIX}/push/subscriptions`, {
    method: 'POST',
    body: JSON.stringify(subscription.toJSON()),
  })
}

export function deleteViewerPushSubscription(subscription) {
  return requestJson(`${API_PREFIX}/push/subscriptions`, {
    method: 'DELETE',
    body: JSON.stringify(subscription.toJSON()),
  })
}
