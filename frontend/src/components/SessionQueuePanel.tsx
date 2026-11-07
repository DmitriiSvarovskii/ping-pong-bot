import type { SessionParticipant, SessionQueue } from '../types/gameSessions'
import { getDisplayName } from '../hooks/useDisplayName'

export function SessionQueuePanel({ queue, participants }: { queue: SessionQueue; participants: SessionParticipant[] }) {
  const profiles = new Map(participants.map((item) => [item.user.id, item.user]))
  const name = (userId: string) => {
    const user = profiles.get(userId)
    return user ? getDisplayName(user) : 'Игрок'
  }
  if (queue.is_paused) return <section className="section-card"><div className="section-title"><h2>Очередь</h2><span>Пауза</span></div><p className="muted-copy">Нужен ещё один участник, чтобы продолжить игру.</p></section>
  return <section className="section-card"><div className="section-title"><h2>За столом</h2><span>{queue.current_pair.length}/2</span></div><div className="current-pair">{queue.current_pair.map((item, index) => <div className="player-chip current" key={item.participation_id}><span>{index === 0 ? 'A' : 'B'}</span><strong>{name(item.user_id)}</strong></div>)}</div><div className="section-title queue-title"><h3>Ожидают</h3><span>{queue.waiting.length}</span></div>{queue.waiting.length ? <ol className="waiting-list">{queue.waiting.map((item, index) => <li key={item.participation_id}><span>{index + 1}</span><strong>{name(item.user_id)}</strong>{index === 0 && <small>Следующий</small>}</li>)}</ol> : <p className="muted-copy">Очередь пуста — играют те же двое.</p>}</section>
}
