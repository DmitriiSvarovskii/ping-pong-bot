import { useState, type ReactNode } from 'react'

import { ApiError } from '../api/client'
import type { SessionStatus } from '../types/gameSessions'

const statusLabels: Record<SessionStatus, string> = {
  pending: 'Ожидает старта',
  active: 'Идёт игра',
  completed: 'Завершена',
  cancelled: 'Отменена',
}

export function StatusBadge({ status }: { status: SessionStatus }) {
  return <span className={`status-badge status-${status}`}>{statusLabels[status]}</span>
}

export function LoadingPanel({ label = 'Загружаем игру…' }: { label?: string }) {
  return <div className="state-panel"><div className="loader"/><p>{label}</p></div>
}

export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return <div className="state-panel error-panel"><strong>Не удалось загрузить данные</strong><p>{sessionErrorMessage(error)}</p><button className="secondary-button" onClick={() => onRetry()}>Повторить</button></div>
}

export function ConfirmAction({
  children,
  confirmation,
  onConfirm,
  disabled = false,
  danger = false,
}: {
  children: ReactNode
  confirmation: string
  onConfirm: () => void
  disabled?: boolean
  danger?: boolean
}) {
  const [confirming, setConfirming] = useState(false)
  if (!confirming) return <button className={danger ? 'danger-button' : 'secondary-button'} disabled={disabled} onClick={() => setConfirming(true)}>{children}</button>
  return <div className="confirm-action"><p>{confirmation}</p><div><button className="quiet-button" onClick={() => setConfirming(false)}>Назад</button><button className={danger ? 'danger-button' : 'primary-button'} disabled={disabled} onClick={() => onConfirm()}>Подтвердить</button></div></div>
}

// Kept with the session UI states so all screens expose consistent safe messages.
// eslint-disable-next-line react-refresh/only-export-components
export function sessionErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 400) return 'Проверьте счёт и параметры игры.'
    if (error.status === 403) return 'У вас нет прав для этого действия.'
    if (error.status === 404) return 'Игра или матч больше не существует.'
    if (error.status === 409) return 'Состояние игры уже изменилось. Обновите данные.'
    return error.message
  }
  return 'Проверьте соединение и попробуйте снова.'
}
