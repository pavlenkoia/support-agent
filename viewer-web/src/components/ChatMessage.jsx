import { formatClockTime } from '../utils/time'

export function ChatMessage({ message, theme }) {
  const isInbound = message.direction === 'inbound'
  const isDark = theme === 'dark'

  return (
    <div className={`flex ${isInbound ? 'justify-start' : 'justify-end'}`}>
      <div
        className={[
          'max-w-[80%] rounded-3xl px-4 py-3 shadow-sm',
          isInbound
            ? isDark
              ? 'bg-slate-800 text-slate-100'
              : 'bg-slate-100 text-slate-900'
            : isDark
              ? 'bg-sky-600 text-white'
              : 'bg-sky-500 text-white',
        ].join(' ')}
      >
        <div
          className={[
            'mb-1 text-xs font-medium uppercase tracking-[0.16em]',
            isInbound ? (isDark ? 'text-slate-400' : 'text-slate-500') : 'text-white/70',
          ].join(' ')}
        >
          {message.author_name}
        </div>
        <div className="whitespace-pre-wrap break-words text-sm leading-6">{message.text}</div>
        <div
          className={[
            'mt-2 text-right text-xs',
            isInbound ? (isDark ? 'text-slate-400' : 'text-slate-500') : 'text-white/70',
          ].join(' ')}
        >
          {formatClockTime(message.sent_at)}
        </div>
      </div>
    </div>
  )
}
