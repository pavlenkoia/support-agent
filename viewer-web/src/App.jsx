import { useEffect, useState } from 'react'

import { ViewerPage } from './pages/ViewerPage'
import { ViewerLoginPage } from './components/ViewerLoginPage'
import { ViewerAuthError, fetchViewerAuthStatus, loginToViewer } from './api/viewer'

function isOfflineError() {
  return typeof navigator !== 'undefined' && navigator.onLine === false
}

export default function App() {
  const [authState, setAuthState] = useState('checking')
  const [authError, setAuthError] = useState('')
  const [loginPending, setLoginPending] = useState(false)

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
      <div className="flex min-h-[100dvh] items-center justify-center bg-slate-100 px-4 text-sm text-slate-600">
        Проверяю авторизацию…
      </div>
    )
  }

  if (authState === 'offline') {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-slate-100 px-4 py-10 text-slate-900">
        <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-sky-600">support-agent</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-900">VK dialog viewer</h1>
          <p className="mt-3 text-sm text-slate-600">
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
    return <ViewerLoginPage isSubmitting={loginPending} errorText={authError} onSubmit={handleLogin} />
  }

  return <ViewerPage onUnauthorized={() => setAuthState('unauthenticated')} />
}
