import { ChatMessage } from './ChatMessage'

export function ChatPanel({ dialog, isLoading, theme }) {
  const isDark = theme === 'dark'

  return (
    <section className={["flex h-full min-h-0 flex-1 flex-col", isDark ? 'bg-slate-950' : 'bg-white'].join(' ')}>
      <div className="flex-1 overflow-y-auto px-6 py-6">
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
