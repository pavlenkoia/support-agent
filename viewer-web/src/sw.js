import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching'

import { notificationClickTarget } from './utils/web-push'

precacheAndRoute(self.__WB_MANIFEST)
cleanupOutdatedCaches()

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('push', (event) => {
  let payload = {}
  try {
    payload = event.data?.json() ?? {}
  } catch {
    payload = {}
  }

  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    windows.forEach((client) => client.postMessage({ type: 'viewer_user_message', payload }))
    await self.registration.showNotification('Новое сообщение в VK', {
      body: payload.case_id ? `Обращение №${payload.case_id}` : 'Откройте viewer для просмотра диалога.',
      data: payload,
      icon: '/pwa-192x192.png',
      badge: '/pwa-192x192.png',
      tag: `viewer-dialog-${payload.conversation_id ?? 'unknown'}`,
      renotify: true,
      requireInteraction: true,
      vibrate: [200, 100, 200],
    })
  })())
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = notificationClickTarget(event.notification.data)
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const existing = windows[0]
    if (existing) {
      await existing.navigate(target)
      await existing.focus()
      return
    }
    await self.clients.openWindow(target)
  })())
})
