import { useState } from 'react'

export function ViewerLoginPage({ isSubmitting, errorText, onSubmit }) {
  const [key, setKey] = useState('')

  const handleSubmit = async (event) => {
    event.preventDefault()
    await onSubmit(key)
  }

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-slate-100 px-4 py-10 text-slate-900">
      <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-sky-600">support-agent</p>
        <h1 className="mt-2 text-2xl font-semibold text-slate-900">VK dialog viewer</h1>
        <p className="mt-3 text-sm text-slate-600">
          Введите ключ доступа, чтобы открыть viewer.
        </p>

        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Ключ доступа</span>
            <input
              autoComplete="current-password"
              className="w-full rounded-2xl border border-slate-300 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-sky-500"
              disabled={isSubmitting}
              onChange={(event) => setKey(event.target.value)}
              placeholder="Введите ключ"
              type="password"
              value={key}
            />
          </label>

          {errorText ? (
            <div className="rounded-2xl border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-700">
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
