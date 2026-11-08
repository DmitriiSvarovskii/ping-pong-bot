import type { SessionMatch } from '../types/gameSessions'
import { getDisplayName } from '../hooks/useDisplayName'

export function SessionHistory({ matches }: { matches: SessionMatch[] }) {
  const completed = matches.filter((match) => match.status === 'completed' || match.status === 'cancelled')
  return <section className="section-card"><div className="section-title"><h2>История матчей</h2><span>{completed.length}</span></div>{completed.length ? <div className="match-history">{completed.map((match) => {
    const first = match.participants.find((item) => item.position === 1)?.user
    const second = match.participants.find((item) => item.position === 2)?.user
    const setScore = match.sets.reduce<[number, number]>(
      ([left, right], set) => set.player1_score > set.player2_score
        ? [left + 1, right]
        : [left, right + 1],
      [0, 0],
    )
    const score = `${setScore[0]}:${setScore[1]}`
    const winner = match.participants.find((item) => item.user.id === match.winner_id)?.user
    return <article key={match.id}><div><span>Матч #{match.sequence_number}</span><small>{match.status === 'cancelled' ? 'Отменён / пропущен' : score}</small></div><p>{first ? getDisplayName(first) : 'Игрок A'} <b>—</b> {second ? getDisplayName(second) : 'Игрок B'}</p>{match.status === 'completed' && <time dateTime={match.completed_at ?? undefined}>{formatCompletedAt(match.completed_at)}</time>}{winner && <strong className="winner-label">Победитель: {getDisplayName(winner)}</strong>}</article>
  })}</div> : <p className="muted-copy">Завершённых матчей пока нет.</p>}</section>
}

function formatCompletedAt(value: string | null): string {
  if (!value) return 'Время завершения неизвестно'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Время завершения неизвестно'
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
