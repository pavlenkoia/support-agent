import { useEffect, useMemo, useState } from 'react'

const MOBILE_MEDIA_QUERY = '(max-width: 640px)'

import { ViewerAuthError, fetchDialogMessages, fetchDialogs } from '../api/viewer'
import { ChatPanel } from '../components/ChatPanel'
import { DialogList } from '../components/DialogList'
import { ViewerHeader } from '../components/ViewerHeader'
import { formatToday } from '../utils/time'

const THEME_STORAGE_KEY = 'vk-dialog-viewer-theme'

function loadInitialTheme() {
  if (typeof window === 'undefined') return 'light'
  return window.localStorage.getItem(THEME_STORAGE_KEY) || 'light'
}

function detectMobileViewport() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(MOBILE_MEDIA_QUERY).matches
}

export function ViewerPage({ onUnauthorized }) {
  const [selectedDay, setSelectedDay] = useState(formatToday)
  const [theme, setTheme] = useState(loadInitialTheme)
  const [dialogs, setDialogs] = useState([])
  const [selectedConversationId, setSelectedConversationId] = useState(null)
  const [dialogMessages, setDialogMessages] = useState(null)
  const [dialogsLoading, setDialogsLoading] = useState(false)
  const [messagesLoading, setMessagesLoading] = useState(false)
  const [errorText, setErrorText] = useState('')
  const [reloadToken, setReloadToken] = useState(0)
  const [isMobileViewport, setIsMobileViewport] = useState(detectMobileViewport)
  const [mobileDialogOpen, setMobileDialogOpen] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined

    const mediaQuery = window.matchMedia(MOBILE_MEDIA_QUERY)
    const syncViewport = (event) => {
      const matches = typeof event?.matches === 'boolean' ? event.matches : mediaQuery.matches
      setIsMobileViewport(matches)
      if (!matches) setMobileDialogOpen(false)
    }

    syncViewport()
    mediaQuery.addEventListener('change', syncViewport)
    return () => mediaQuery.removeEventListener('change', syncViewport)
  }, [])

  useEffect(() => {
    let cancelled = false
    setDialogsLoading(true)
    setErrorText('')

    fetchDialogs(selectedDay)
      .then((items) => {
        if (cancelled) return
        setDialogs(items)
        const firstConversationId = items[0]?.conversation_id ?? null
        setSelectedConversationId(firstConversationId)
        setMobileDialogOpen(false)
        if (!firstConversationId) {
          setDialogMessages(null)
        }
      })
      .catch((error) => {
        if (cancelled) return
        if (error instanceof ViewerAuthError) {
          onUnauthorized?.()
          return
        }
        setDialogs([])
        setSelectedConversationId(null)
        setDialogMessages(null)
        setMobileDialogOpen(false)
        setErrorText('Не удалось загрузить диалоги.')
      })
      .finally(() => {
        if (!cancelled) setDialogsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedDay, reloadToken, onUnauthorized])

  useEffect(() => {
    if (!selectedConversationId) {
      setDialogMessages(null)
      setMessagesLoading(false)
      return
    }

    let cancelled = false
    setMessagesLoading(true)
    setErrorText('')

    fetchDialogMessages(selectedConversationId, selectedDay)
      .then((payload) => {
        if (cancelled) return
        setDialogMessages(payload)
      })
      .catch((error) => {
        if (cancelled) return
        if (error instanceof ViewerAuthError) {
          onUnauthorized?.()
          return
        }
        setDialogMessages(null)
        setErrorText('Не удалось загрузить сообщения диалога.')
      })
      .finally(() => {
        if (!cancelled) setMessagesLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedConversationId, selectedDay, reloadToken, onUnauthorized])

  const activeDialog = useMemo(
    () => dialogs.find((dialog) => dialog.conversation_id === selectedConversationId) ?? null,
    [dialogs, selectedConversationId],
  )

  const showMobileDetail = isMobileViewport && mobileDialogOpen

  const chatDialog = dialogMessages
    ? {
        ...activeDialog,
        ...dialogMessages,
      }
    : activeDialog
      ? { ...activeDialog, messages: [] }
      : null

  return (
    <div
      className={[
        'flex h-[100dvh] min-h-[100dvh] flex-col overflow-hidden',
        theme === 'dark' ? 'bg-slate-950 text-slate-100' : 'bg-slate-100 text-slate-900',
      ].join(' ')}
    >
      <ViewerHeader theme={theme} onThemeToggle={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))} />

      <main className="mx-auto flex min-h-0 flex-1 w-full max-w-[1800px] flex-col px-0 md:px-6 md:pb-6">
        {errorText ? (
          <div
            className={[
              'mx-4 mt-4 rounded-2xl border px-4 py-3 text-sm md:mx-0',
              theme === 'dark'
                ? 'border-rose-500/30 bg-rose-500/10 text-rose-200'
                : 'border-rose-300 bg-rose-50 text-rose-700',
            ].join(' ')}
          >
            {errorText}
          </div>
        ) : null}

        <div
          className={[
            'min-h-0 w-full flex-1 overflow-hidden md:mt-6 md:rounded-3xl md:border',
            theme === 'dark' ? 'md:border-slate-800 md:bg-slate-900/20' : 'md:border-slate-200 md:bg-white',
          ].join(' ')}
        >
          <div className={["flex h-full min-h-0 w-full overflow-hidden", isMobileViewport ? 'flex-col' : 'flex-row'].join(' ')}>
            <DialogList
              dialogs={dialogs}
              isLoading={dialogsLoading}
              isRefreshing={dialogsLoading || messagesLoading}
              onDayChange={setSelectedDay}
              onRefresh={() => setReloadToken((current) => current + 1)}
              selectedConversationId={selectedConversationId}
              selectedDay={selectedDay}
              onSelect={(conversationId) => {
                setSelectedConversationId(conversationId)
                const openMobileDetail = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
                  ? window.matchMedia(MOBILE_MEDIA_QUERY).matches
                  : isMobileViewport
                setMobileDialogOpen(openMobileDetail)
              }}
              theme={theme}
              className={isMobileViewport ? 'flex' : 'flex basis-[22rem] min-w-[22rem] max-w-[22rem]'}
            />
            <ChatPanel
              dialog={chatDialog}
              isLoading={messagesLoading}
              theme={theme}
              className={isMobileViewport ? 'hidden' : 'flex min-w-0'}
            />
            {showMobileDetail ? (
              <div className="fixed inset-x-0 bottom-0 top-14 z-30">
                <ChatPanel
                  dialog={chatDialog}
                  isLoading={messagesLoading}
                  theme={theme}
                  className="flex h-full"
                  onBack={() => setMobileDialogOpen(false)}
                />
              </div>
            ) : null}
          </div>
        </div>
      </main>
    </div>
  )
}
