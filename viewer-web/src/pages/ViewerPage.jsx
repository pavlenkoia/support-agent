import { useEffect, useMemo, useState } from 'react'

import { fetchDialogMessages, fetchDialogs } from '../api/viewer'
import { ChatPanel } from '../components/ChatPanel'
import { DialogList } from '../components/DialogList'
import { ViewerHeader } from '../components/ViewerHeader'

const THEME_STORAGE_KEY = 'vk-dialog-viewer-theme'

function formatToday() {
  return new Date().toISOString().slice(0, 10)
}

function loadInitialTheme() {
  if (typeof window === 'undefined') return 'light'
  return window.localStorage.getItem(THEME_STORAGE_KEY) || 'light'
}

export function ViewerPage() {
  const [selectedDay, setSelectedDay] = useState(formatToday)
  const [theme, setTheme] = useState(loadInitialTheme)
  const [dialogs, setDialogs] = useState([])
  const [selectedConversationId, setSelectedConversationId] = useState(null)
  const [dialogMessages, setDialogMessages] = useState(null)
  const [dialogsLoading, setDialogsLoading] = useState(false)
  const [messagesLoading, setMessagesLoading] = useState(false)
  const [errorText, setErrorText] = useState('')
  const [mobileDialogOpen, setMobileDialogOpen] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

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
      .catch(() => {
        if (cancelled) return
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
  }, [selectedDay])

  useEffect(() => {
    if (!selectedConversationId) return

    let cancelled = false
    setMessagesLoading(true)
    setErrorText('')

    fetchDialogMessages(selectedConversationId, selectedDay)
      .then((payload) => {
        if (cancelled) return
        setDialogMessages(payload)
      })
      .catch(() => {
        if (cancelled) return
        setDialogMessages(null)
        setErrorText('Не удалось загрузить сообщения диалога.')
      })
      .finally(() => {
        if (!cancelled) setMessagesLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedConversationId, selectedDay])

  const activeDialog = useMemo(
    () => dialogs.find((dialog) => dialog.conversation_id === selectedConversationId) ?? null,
    [dialogs, selectedConversationId],
  )

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

      <main className="mx-auto flex min-h-0 flex-1 max-w-[1800px] flex-col px-0 md:px-6 md:pb-6">
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
            'min-h-0 flex-1 overflow-hidden md:mt-6 md:rounded-3xl md:border',
            theme === 'dark' ? 'md:border-slate-800 md:bg-slate-900/20' : 'md:border-slate-200 md:bg-white',
          ].join(' ')}
        >
          <div className="flex h-full min-h-0 flex-col overflow-hidden md:flex-row">
            <DialogList
              dialogs={dialogs}
              isLoading={dialogsLoading}
              onDayChange={setSelectedDay}
              selectedConversationId={selectedConversationId}
              selectedDay={selectedDay}
              onSelect={(conversationId) => {
                setSelectedConversationId(conversationId)
                setMobileDialogOpen(true)
              }}
              theme={theme}
              className={mobileDialogOpen ? 'hidden md:flex' : 'flex'}
            />
            <ChatPanel
              dialog={chatDialog}
              isLoading={messagesLoading}
              theme={theme}
              className={mobileDialogOpen ? 'flex' : 'hidden md:flex'}
              onBack={() => setMobileDialogOpen(false)}
            />
          </div>
        </div>
      </main>
    </div>
  )
}
