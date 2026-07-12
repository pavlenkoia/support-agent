import { useEffect, useState } from 'react'

import { ViewerPage } from './pages/ViewerPage'
import { ViewerLoginPage } from './components/ViewerLoginPage'
import { ViewerAuthError, fetchViewerAuthStatus, loginToViewer } from './api/viewer'
import { applyThemeToDocument, loadInitialTheme } from './utils/theme'

function isOfflineError() {
  return typeof navigator !== 'undefined' && navigator.onLine === false
}

export default function App() {
  const [authState, setAuthState] = useState('checking')
  const [authError, setAuthError] = useState('')
  const [loginPending, setLoginPending] = useState(false)
  const [theme, setTheme] = useState(loadInitialTheme)
  const isDark = theme === 'dark'

  useEffect(() => {
    applyThemeToDocument(theme)
  }, [theme])

  useEffect(() => {
    let cancelled = false

    fetchViewerAuthStatus()
      .then((payload) => {
        if (cancelled) return
        setAuthState(payload.authenticated ? 'authenticated' : 'unauthenticated')
      })
      .catch(() => {
        if (cancelled) return
        if (isOfflineError()) {
          setAuthState('offline')
          return
        }
        setAuthState('unauthenticated')
      })

    return () => {
      cancelled = true
    }
  }, [])

  const handleLogin = async (key) => {
    setLoginPending(true)
    setAuthError('')
    try {
      await loginToViewer(key)
      const payload = await fetchViewerAuthStatus()
      setAuthState(payload.authenticated ? 'authenticated' : 'unauthenticated')
      if (!payload.authenticated) {
        setAuthError('Не удалось сохранить авторизацию.')
      }
    } catch (error) {
      if (isOfflineError()) {
        setAuthState('offline')
        setAuthError('')
      } else if (error instanceof ViewerAuthError) {
        setAuthError('Неверный ключ доступа.')
      } else {
        setAuthError('Не удалось выполнить вход.')
      }
      if (!isOfflineError()) {
        setAuthState('unauthenticated')
      }
    } finally {
      setLoginPending(false)
    }
  }

  if (authState === 'checking') {
    return (
      <div
        className={[
          'flex min-h-[100dvh] items-center justify-center px-4 text-sm',
          isDark ? 'bg-slate-950 text-slate-300' : 'bg-slate-100 text-slate-600',
        ].join(' ')}
      >
        Проверяю авторизацию…
      </div>
    )
  }

  if (authState === 'offline') {
    return (
      <div
        className={[
          'flex min-h-[100dvh] items-center justify-center px-4 py-10',
          isDark ? 'bg-slate-950 text-slate-100' : 'bg-slate-100 text-slate-900',
        ].join(' ')}
      >
        <div
          className={[
            'w-full max-w-md rounded-3xl border p-6 shadow-sm md:p-8',
            isDark ? 'border-slate-800 bg-slate-900' : 'border-slate-200 bg-white',
          ].join(' ')}
        >
          <p className={['text-xs font-semibold uppercase tracking-[0.16em]', isDark ? 'text-sky-400' : 'text-sky-600'].join(' ')}>
            support-agent
          </p>
          <h1 className={['mt-2 text-2xl font-semibold', isDark ? 'text-slate-100' : 'text-slate-900'].join(' ')}>
            VK dialog viewer
          </h1>
          <p className={['mt-3 text-sm', isDark ? 'text-slate-400' : 'text-slate-600'].join(' ')}>
            Viewer установлен и оболочка доступна, но сейчас нет сети. Подключите интернет и откройте приложение снова.
          </p>
          <button
            className="mt-6 inline-flex w-full items-center justify-center rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition hover:bg-sky-700"
            onClick={() => window.location.reload()}
            type="button"
          >
            Повторить
          </button>
        </div>
      </div>
    )
  }

  if (authState !== 'authenticated') {
    return <ViewerLoginPage isSubmitting={loginPending} errorText={authError} onSubmit={handleLogin} theme={theme} />
  }

  return <ViewerPage onUnauthorized={() => setAuthState('unauthenticated')} />
}
