import { useEffect, useRef, useState } from 'react'

import { ChatMessage } from './ChatMessage'

const EDGE_SWIPE_START_RATIO = 0.4
const EDGE_SWIPE_MAX_START_X_CAP = 220
const EDGE_SWIPE_MIN_DELTA_X = 48
const EDGE_SWIPE_MAX_DELTA_Y = 96
const SWIPE_CLOSE_ANIMATION_MS = 180
const SWIPE_CLOSE_RATIO = 0.18

export function ChatPanel({
  dialog,
  isLoading,
  theme,
  className = '',
  onBack = null,
  onSwipeProgress = null,
}) {
  const isDark = theme === 'dark'
  const closeTimerRef = useRef(null)
  const swipeStateRef = useRef({
    startX: null,
    startY: null,
    tracking: false,
    panelWidth: 0,
  })

  const [dragOffsetX, setDragOffsetX] = useState(0)
  const [isDragging, setIsDragging] = useState(false)

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) {
        window.clearTimeout(closeTimerRef.current)
      }
      onSwipeProgress?.(0)
    }
  }, [onSwipeProgress])

  const resetSwipeState = ({ keepOffset = false } = {}) => {
    swipeStateRef.current = {
      startX: null,
      startY: null,
      tracking: false,
      panelWidth: 0,
    }
    setIsDragging(false)
    onSwipeProgress?.(0)
    if (!keepOffset) {
      setDragOffsetX(0)
    }
  }

  const finishBackNavigation = () => {
    const width = swipeStateRef.current.panelWidth || (typeof window !== 'undefined' ? window.innerWidth : 0)
    setIsDragging(false)
    setDragOffsetX(width)
    onSwipeProgress?.(1)

    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current)
    }

    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null
      onBack?.({ completedBySwipe: true })
      setDragOffsetX(0)
      onSwipeProgress?.(0)
    }, SWIPE_CLOSE_ANIMATION_MS)
  }

  const handleTouchStart = (event) => {
    if (!onBack) return
    const touch = event.touches?.[0]
    if (!touch) return

    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }

    const panelWidth = event.currentTarget.clientWidth || 0
    const swipeStartLimit = panelWidth > 0
      ? Math.min(panelWidth * EDGE_SWIPE_START_RATIO, EDGE_SWIPE_MAX_START_X_CAP)
      : EDGE_SWIPE_MAX_START_X_CAP

    swipeStateRef.current = {
      startX: touch.clientX,
      startY: touch.clientY,
      tracking: touch.clientX <= swipeStartLimit,
      panelWidth,
    }

    setIsDragging(false)
    setDragOffsetX(0)
    onSwipeProgress?.(0)
  }

  const handleTouchMove = (event) => {
    if (!onBack) return

    const { startX, startY, tracking, panelWidth } = swipeStateRef.current
    const touch = event.touches?.[0]

    if (!tracking || startX === null || startY === null || !touch) {
      return
    }

    const deltaX = touch.clientX - startX
    const deltaY = touch.clientY - startY
    const horizontalTravel = Math.abs(deltaX)
    const verticalTravel = Math.abs(deltaY)

    if (deltaX <= 0 || verticalTravel > EDGE_SWIPE_MAX_DELTA_Y || horizontalTravel <= verticalTravel) {
      setIsDragging(false)
      setDragOffsetX(0)
      return
    }

    const maxOffset = panelWidth || (typeof window !== 'undefined' ? window.innerWidth : deltaX)
    const nextOffset = Math.min(deltaX, maxOffset)
    setIsDragging(true)
    setDragOffsetX(nextOffset)
    onSwipeProgress?.(maxOffset > 0 ? Math.min(nextOffset / maxOffset, 1) : 0)
  }

  const handleTouchEnd = (event) => {
    if (!onBack) return

    const { startX, startY, tracking, panelWidth } = swipeStateRef.current
    const touch = event.changedTouches?.[0]

    if (!tracking || startX === null || startY === null || !touch) {
      resetSwipeState()
      return
    }

    const deltaX = touch.clientX - startX
    const deltaY = touch.clientY - startY
    const horizontalTravel = Math.abs(deltaX)
    const verticalTravel = Math.abs(deltaY)
    const width = panelWidth || (typeof window !== 'undefined' ? window.innerWidth : 0)

    const shouldGoBack =
      deltaX >= EDGE_SWIPE_MIN_DELTA_X &&
      verticalTravel <= EDGE_SWIPE_MAX_DELTA_Y &&
      horizontalTravel > verticalTravel &&
      (width === 0 || deltaX >= width * SWIPE_CLOSE_RATIO)

    if (shouldGoBack) {
      resetSwipeState({ keepOffset: true })
      finishBackNavigation()
      return
    }

    resetSwipeState()
  }

  const panelStyle = onBack
    ? {
        touchAction: 'pan-y',
        transform: dragOffsetX > 0 ? `translateX(${dragOffsetX}px)` : 'translateX(0px)',
        transition: isDragging ? 'none' : 'transform 220ms cubic-bezier(0.22, 1, 0.36, 1)',
        willChange: dragOffsetX > 0 ? 'transform' : 'auto',
        boxShadow:
          dragOffsetX > 0
            ? isDark
              ? '-20px 0 40px rgba(2, 6, 23, 0.45)'
              : '-20px 0 40px rgba(15, 23, 42, 0.18)'
            : undefined,
      }
    : undefined

  return (
    <section
      className={[
        'h-full min-h-0 flex-1 flex-col',
        onBack ? (isDark ? 'border-t border-slate-800' : 'border-t border-slate-200') : '',
        isDark ? 'bg-slate-950' : 'bg-white',
        className,
      ].join(' ')}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onTouchCancel={() => resetSwipeState()}
      style={panelStyle}
    >
      {onBack ? (
        <div className={["border-b px-4 py-3 md:hidden", isDark ? 'border-slate-800' : 'border-slate-200'].join(' ')}>
          <div className="flex items-center gap-3">
            <button
              aria-label="К списку"
              title="К списку"
              className={[
                'inline-flex h-10 w-10 items-center justify-center rounded-xl border transition',
                isDark
                  ? 'border-slate-700 bg-slate-900 text-slate-100 hover:border-slate-600'
                  : 'border-slate-300 bg-white text-slate-900 hover:border-slate-400',
              ].join(' ')}
              onClick={onBack}
              type="button"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-5 w-5"
                aria-hidden="true"
              >
                <path d="M15 18l-6-6 6-6" />
              </svg>
            </button>
            <div className={["text-sm font-semibold", isDark ? 'text-slate-100' : 'text-slate-900'].join(' ')}>
              {dialog?.case_id ? `Обращение №${dialog.case_id}` : 'Сообщение'}
            </div>
          </div>
        </div>
      ) : null}
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
