import { ChatMessage } from './ChatMessage'

export function ChatPanel({ dialog, isLoading, theme, className = '', onBack }) {
  const isDark = theme === 'dark'

  return (
    <section className={[
      'h-full min-h-0 flex-1 flex-col',
      isDark ? 'bg-slate-950' : 'bg-white',
      className,
    ].join(' ')}>
      <div className={["border-b px-4 py-3 md:hidden", isDark ? 'border-slate-800' : 'border-slate-200'].join(' ')}>
        <button
          className={[
            'inline-flex items-center rounded-xl border px-3 py-2 text-sm transition',
            isDark
              ? 'border-slate-700 bg-slate-900 text-slate-100 hover:border-slate-600'
              : 'border-slate-300 bg-white text-slate-900 hover:border-slate-400',
          ].join(' ')}
          onClick={onBack}
          type="button"
        >
          ← К списку
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        {isLoading ? (
          <div
            className={[
              'rounded-2xl border px-4 py-6 text-sm',
              isDark ? 'border-slate-800 bg-slate-900/60 text-slate-400' : 'border-slate-200 bg-slate-50 text-slate-500',
            ].join(' ')}
          >
            Загружаю сообщения…
          </div>
        ) : !dialog ? (
          <div
            className={[
              'rounded-2xl border border-dashed px-4 py-6 text-sm',
              isDark ? 'border-slate-800 bg-slate-900/40 text-slate-400' : 'border-slate-300 bg-slate-50 text-slate-500',
            ].join(' ')}
          >
            Выберите диалог слева.
          </div>
        ) : dialog.messages.length === 0 ? (
          <div
            className={[
              'rounded-2xl border border-dashed px-4 py-6 text-sm',
              isDark ? 'border-slate-800 bg-slate-900/40 text-slate-400' : 'border-slate-300 bg-slate-50 text-slate-500',
            ].join(' ')}
          >
            В выбранный день сообщений нет.
          </div>
        ) : (
          <div className="space-y-4">
            {dialog.messages.map((message) => (
              <ChatMessage key={message.id} message={message} theme={theme} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
