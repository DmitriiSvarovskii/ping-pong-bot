import type { SessionMatch } from '../types/gameSessions'
import { ApiError } from '../api/client'
import { sessionErrorMessage } from './SessionUi'

export function VoidMatchModal({
    match,
    pending,
    error,
    onConfirm,
    onClose,
}: {
    match: SessionMatch
    pending: boolean
    error: unknown
    onConfirm: () => void
    onClose: () => void
}) {
    const conflict =
        error instanceof ApiError && error.status === 409

    return (
        <div className="modal-backdrop" role="presentation">
            <section
                className="skip-modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="void-match-title"
            >
                <div className="section-title">
                    <h2 id="void-match-title">Аннулировать матч?</h2>

                    <button
                        type="button"
                        className="modal-close"
                        aria-label="Закрыть"
                        disabled={pending}
                        onClick={onClose}
                    >
                        ×
                    </button>
                </div>

                <strong>Матч #{match.sequence_number}</strong>

                <p>
                    {match.is_ranked
                        ? 'Результат матча будет исключён из статистики и рейтинга. Если матч уже учтён в рейтинге, будет выполнен пересчёт.'
                        : 'Результат матча будет исключён из статистики.'}
                </p>

                <p>
                    Восстановить матч после аннулирования невозможно.
                </p>

                {error != null && (
                    <p className="form-error" role="alert">
                        {conflict
                            ? 'Не удалось аннулировать матч. Закройте окно, обновите данные и проверьте результат.'
                            : sessionErrorMessage(error)}
                    </p>
                )}

                <button
                    type="button"
                    className="danger-button"
                    disabled={pending || conflict}
                    onClick={onConfirm}
                >
                    {pending ? 'Аннулируем…' : 'Аннулировать'}
                </button>

                <button
                    type="button"
                    className="secondary-button"
                    disabled={pending}
                    onClick={onClose}
                >
                    Отмена
                </button>
            </section>
        </div>
    )
}