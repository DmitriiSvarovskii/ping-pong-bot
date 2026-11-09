import { useMutation } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { cancelCurrentMatch, skipCurrentMatch, transitionSession } from '../api/gameSessions'
import { shareSessionInvitation } from '../auth/telegram'
import { useAuth } from '../auth/AuthProvider'
import { SessionHistory } from '../components/SessionHistory'
import { SessionLeaderboard } from '../components/SessionLeaderboard'
import { SessionQueuePanel } from '../components/SessionQueuePanel'
import { SkipMatchModal } from '../components/SkipMatchModal'
import {
  ConfirmAction,
  ErrorPanel,
  LoadingPanel,
  sessionErrorMessage,
  StatusBadge,
} from '../components/SessionUi'
import { getDisplayName } from '../hooks/useDisplayName'
import { useGameSession, useInvalidateSession } from '../hooks/useGameSession'
import { useTelegramBackButton } from '../hooks/useTelegramBackButton'
import { useSessionRealtime } from '../realtime/useSessionRealtime'

type SessionAction = 'leave' | 'start' | 'complete' | 'cancel' | 'cancel-and-leave'

export function SessionPage() {
  const { sessionId = '' } = useParams()
  const navigate = useNavigate()
  const { user, apiClient } = useAuth()
  const data = useGameSession(sessionId)
  useSessionRealtime(sessionId)
  const invalidate = useInvalidateSession()
  const returnToGames = useCallback(() => { void navigate('/games') }, [navigate])
  useTelegramBackButton(returnToGames)
  const [shareMessage, setShareMessage] = useState<string | null>(null)
  const [skipOpen, setSkipOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: async (action: SessionAction) => {
      if (action === 'cancel-and-leave') {
        const current = data.currentMatch.data
        if (!current) throw new Error('Current match is unavailable')
        return cancelCurrentMatch(apiClient, sessionId, current.id, true)
      }
      return transitionSession(apiClient, sessionId, action)
    },
    onSuccess: async (_, action) => {
      await invalidate(sessionId)
      if (action === 'leave' || action === 'cancel' || action === 'cancel-and-leave') {
        void navigate('/games')
      }
    },
  })
  const skipMutation = useMutation({
    mutationFn: (skippedPlayerId: string | null) => {
      const current = data.currentMatch.data
      if (!current) throw new Error('Current match is unavailable')
      return skipCurrentMatch(apiClient, sessionId, current.id, skippedPlayerId)
    },
    onSuccess: async () => {
      await invalidate(sessionId)
      setSkipOpen(false)
    },
  })

  if (data.detail.isPending) return <LoadingPanel />
  if (!data.detail.data) return <ErrorPanel error={data.detail.error} onRetry={() => void data.detail.refetch()} />

  const session = data.detail.data
  const participants = data.participants.data?.items ?? session.active_participants
  const queue = data.queue.data
  const current = data.currentMatch.data
  const isOrganizer = user?.id === session.organizer_id
  const isParticipant = participants.some((item) => item.user.id === user?.id)
  const isCurrentPlayer = queue?.current_pair.some((item) => item.user_id === user?.id) ?? false
  const isWaiting = queue?.waiting.some((item) => item.user_id === user?.id) ?? false
  const organizer = participants.find((item) => item.user.id === session.organizer_id)?.user
  const actionPending = mutation.isPending

  async function share() {
    try {
      const destination = await shareSessionInvitation(session.id)
      setShareMessage(destination === 'telegram' ? 'Открыто меню Telegram' : 'Ссылка скопирована')
    } catch {
      setShareMessage('Не удалось поделиться ссылкой')
    }
  }

  return <div className="page-stack session-page"><header className="session-hero"><div><StatusBadge status={session.status}/><h1>{session.name || 'Игра без названия'}</h1><p>{session.points_to_win} очков · до {session.sets_to_win} побед {session.is_ranked ? '· рейтинговая' : '· нерейтинговая'}</p></div><button className="share-button" onClick={() => void share()}>Пригласить</button>{shareMessage && <small className="share-message">{shareMessage}</small>}</header>

  {mutation.error && <div className="inline-error">{sessionErrorMessage(mutation.error)}</div>}

  {session.status === 'active' && current ? <section className="current-match-card"><div className="section-title"><span>Текущий матч</span><strong>#{current.sequence_number}</strong></div><div className="versus">{[...current.participants].sort((a, b) => a.position - b.position).map((item, index) => <div key={item.user.id}><span>{index === 0 ? 'A' : 'B'}</span><strong className="current-match-player-name">{getDisplayName(item.user)}</strong></div>)}</div><div className="current-match-actions">{(isOrganizer || isCurrentPlayer) && <button className="secondary-button" disabled={actionPending || skipMutation.isPending} onClick={() => { skipMutation.reset(); setSkipOpen(true) }}>Пропустить</button>}{isParticipant && <Link className="primary-button" to={`/games/${session.id}/result`}>Записать результат</Link>}</div></section> : session.status === 'active' ? <LoadingPanel label={queue?.is_paused ? 'Сессия на паузе' : 'Загружаем текущий матч…'} /> : null}

  {queue && <SessionQueuePanel queue={queue} participants={participants} />}

  <section className="section-card"><div className="section-title"><h2>Участники</h2><span>{participants.length}</span></div><div className="participants-list">{participants.map((item) => <div key={item.participation_id}><span className="participant-avatar">{item.user.first_name.slice(0, 1).toUpperCase()}</span><strong>{getDisplayName(item.user)}</strong><small>{item.user.id === session.organizer_id ? 'Организатор' : item.user.id === user?.id ? 'Вы' : ''}</small></div>)}</div>{organizer && <p className="muted-copy">Организатор: {getDisplayName(organizer)}</p>}</section>

  {data.statistics.error && !data.statistics.data
    ? <ErrorPanel error={data.statistics.error} onRetry={() => void data.statistics.refetch()} />
    : <SessionLeaderboard items={data.statistics.data?.items ?? []} />}
  <SessionHistory matches={data.matches.data?.items ?? []} />

  <section className="session-actions"><h2>Управление</h2>{session.status === 'pending' && isOrganizer && <><button className="primary-button" disabled={actionPending || participants.length < 2} onClick={() => mutation.mutate('start')}>Начать игру</button>{participants.length < 2 && <small>Для старта нужны минимум два участника.</small>}<ConfirmAction danger disabled={actionPending} confirmation="Сессия будет отменена. Это действие нельзя продолжить." onConfirm={() => mutation.mutate('cancel')}>Отменить сессию</ConfirmAction></>}{session.status === 'pending' && !isOrganizer && isParticipant && <ConfirmAction disabled={actionPending} confirmation="Вы покинете эту сессию." onConfirm={() => mutation.mutate('leave')}>Покинуть</ConfirmAction>}{session.status === 'active' && isOrganizer && <ConfirmAction danger disabled={actionPending} confirmation="Сессия завершится, а текущий матч будет отменён без результата." onConfirm={() => mutation.mutate('complete')}>Завершить всю сессию</ConfirmAction>}{session.status === 'active' && !isOrganizer && isWaiting && <ConfirmAction disabled={actionPending} confirmation="Вы покинете очередь ожидания." onConfirm={() => mutation.mutate('leave')}>Покинуть очередь</ConfirmAction>}{session.status === 'active' && !isOrganizer && isCurrentPlayer && current && <ConfirmAction danger disabled={actionPending} confirmation="Текущий матч отменится без победителя, после чего вы покинете сессию." onConfirm={() => mutation.mutate('cancel-and-leave')}>Отменить матч и выйти</ConfirmAction>}</section>
  {skipOpen && current && <SkipMatchModal participants={current.participants} pending={skipMutation.isPending} error={skipMutation.error} onSkip={(playerId) => skipMutation.mutate(playerId)} onClose={() => { if (!skipMutation.isPending) setSkipOpen(false) }} />}
  </div>
}
