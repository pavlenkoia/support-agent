import { useEffect, useState } from 'react'

import { ViewerPage } from './pages/ViewerPage'
import { ViewerLoginPage } from './components/ViewerLoginPage'
import { ViewerAuthError, fetchViewerAuthStatus, loginToViewer } from './api/viewer'

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
      if (error instanceof ViewerAuthError) {
        setAuthError('Неверный ключ доступа.')
      } else {
        setAuthError('Не удалось выполнить вход.')
      }
      setAuthState('unauthenticated')
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

  if (authState !== 'authenticated') {
    return <ViewerLoginPage isSubmitting={loginPending} errorText={authError} onSubmit={handleLogin} />
  }

  return <ViewerPage onUnauthorized={() => setAuthState('unauthenticated')} />
}
