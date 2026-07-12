import { useEffect, useState } from 'react'

import { ViewerPage } from './pages/ViewerPage'
import { ViewerLoginPage } from './components/ViewerLoginPage'
import { ViewerAuthError, fetchViewerAuthStatus, loginToViewer } from './api/viewer'
import { applyThemeToDocument, loadInitialTheme } from './utils/theme'

function isOfflineError() {
  return typeof navigator !== 'undefined' && navigator.onLine === false
}

function AuthCheckingSplash({ isDark }) {
  return (
    <div
      className={[
        'flex min-h-[100dvh] items-center justify-center px-6',
        isDark ? 'bg-slate-950 text-slate-100' : 'bg-slate-100 text-slate-900',
      ].join(' ')}
    >
      <div className="flex items-center justify-center">
        <div className="relative flex h-20 w-20 items-center justify-center">
          <div
            className={[
              'absolute inset-0 rounded-full animate-ping',
              isDark ? 'bg-sky-500/20' : 'bg-sky-500/15',
            ].join(' ')}
            style={{ animationDuration: '1.6s' }}
          />
          <div
            className={[
              'absolute inset-2 rounded-full',
              isDark ? 'bg-sky-400/18' : 'bg-sky-400/12',
            ].join(' ')}
          />
          <div
            className={[
              'relative flex h-16 w-16 items-center justify-center rounded-full border shadow-[0_12px_40px_rgba(14,165,233,0.24)]',
              isDark ? 'border-sky-400/30 bg-slate-900 text-sky-300' : 'border-sky-300/60 bg-white text-sky-600',
            ].join(' ')}
            style={{ animation: 'viewer-splash-pulse 1.5s ease-in-out infinite' }}
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 18l-3 2V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7z" />
              <path d="M8 9h8" />
              <path d="M8 13h5" />
            </svg>
          </div>
        </div>
      </div>
    </div>
  )
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
    return <AuthCheckingSplash isDark={isDark} />
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
