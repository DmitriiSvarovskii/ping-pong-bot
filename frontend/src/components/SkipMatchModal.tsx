import { getDisplayName } from '../hooks/useDisplayName'
import type { MatchParticipant } from '../types/gameSessions'
import { sessionErrorMessage } from './SessionUi'

export function SkipMatchModal({
  participants,
  pending,
  error,
  onSkip,
  onClose,
}: {
  participants: MatchParticipant[]
  pending: boolean
  error: unknown
  onSkip: (playerId: string | null) => void
  onClose: () => void
}) {
  const players = [...participants].sort((a, b) => a.position - b.position)
  return <div className="modal-backdrop" role="presentation"><section className="skip-modal" role="dialog" aria-modal="true" aria-labelledby="skip-match-title"><div className="section-title"><h2 id="skip-match-title">Кого пропустить?</h2><button type="button" className="modal-close" aria-label="Закрыть" disabled={pending} onClick={onClose}>×</button></div><p>Выбранные игроки переместятся в конец очереди. Матч завершится без результата.</p>{error != null && <p className="form-error">{sessionErrorMessage(error)}</p>}<div className="skip-options">{players.map((player) => <button type="button" className="secondary-button" disabled={pending} key={player.user.id} onClick={() => onSkip(player.user.id)}>{pending ? 'Обновляем…' : getDisplayName(player.user)}</button>)}<button type="button" className="danger-button" disabled={pending} onClick={() => onSkip(null)}>{pending ? 'Обновляем…' : 'Обоих игроков'}</button></div><button type="button" className="quiet-button" disabled={pending} onClick={onClose}>Закрыть без изменений</button></section></div>
}
