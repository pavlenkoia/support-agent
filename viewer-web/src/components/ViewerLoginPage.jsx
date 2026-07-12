import { useState } from 'react'

export function ViewerLoginPage({ isSubmitting, errorText, onSubmit, theme = 'light' }) {
  const [key, setKey] = useState('')
  const isDark = theme === 'dark'

  const handleSubmit = async (event) => {
    event.preventDefault()
    await onSubmit(key)
  }

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
          Введите ключ доступа, чтобы открыть viewer.
        </p>

        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          <label className="block">
            <span className={['mb-2 block text-sm font-medium', isDark ? 'text-slate-300' : 'text-slate-700'].join(' ')}>
              Ключ доступа
            </span>
            <input
              autoComplete="current-password"
              className={[
                'w-full rounded-2xl border px-4 py-3 text-sm outline-none transition focus:border-sky-500',
                isDark ? 'border-slate-700 bg-slate-950 text-slate-100' : 'border-slate-300 bg-white text-slate-900',
              ].join(' ')}
              disabled={isSubmitting}
              onChange={(event) => setKey(event.target.value)}
              placeholder="Введите ключ"
              type="password"
              value={key}
            />
          </label>

          {errorText ? (
            <div
              className={[
                'rounded-2xl border px-4 py-3 text-sm',
                isDark ? 'border-rose-500/30 bg-rose-500/10 text-rose-200' : 'border-rose-300 bg-rose-50 text-rose-700',
              ].join(' ')}
            >
              {errorText}
            </div>
          ) : null}

          <button
            className="inline-flex w-full items-center justify-center rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition hover:bg-sky-700 disabled:cursor-wait disabled:opacity-70"
            disabled={isSubmitting || !key.trim()}
            type="submit"
          >
            {isSubmitting ? 'Проверяю…' : 'Войти'}
          </button>
        </form>
      </div>
    </div>
  )
}
