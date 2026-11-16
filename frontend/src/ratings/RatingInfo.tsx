import { useEffect, useId, useRef, useState } from 'react'

const ratingRules = [
    {
        title: 'Начальный рейтинг',
        description:
            'Каждый игрок начинает с рейтинга 1500.',
    },
    {
        title: 'Результаты матчей',
        description:
            'Изменение рейтинга зависит от результатов матчей, силы соперников и надёжности рейтинга игроков.',
    },
    {
        title: 'Обновление рейтинга',
        description:
            'Рейтинг рассчитывается ежедневно. Новые результаты учитываются при следующем расчёте.',
    },
    {
        title: 'Надёжность рейтинга',
        description:
            'Показывает, насколько уверенно система оценивает уровень игрока. Регулярные игры помогают повысить точность оценки.',
    },
    {
        title: 'Перерывы в игре',
        description:
            'При длительном отсутствии матчей надёжность рейтинга снижается.',
    },
    {
        title: 'Товарищеские матчи',
        description:
            'Не влияют на рейтинг игроков.',
    },
]

export function RatingInfo() {
    const [open, setOpen] = useState(false)
    const titleId = useId()
    const triggerRef = useRef<HTMLButtonElement>(null)
    const closeRef = useRef<HTMLButtonElement>(null)

    useEffect(() => {
        if (!open) return

        const trigger = triggerRef.current

        closeRef.current?.focus()

        function handleKeyDown(event: KeyboardEvent) {
            if (event.key === 'Escape') {
                setOpen(false)
            }
        }

        document.addEventListener('keydown', handleKeyDown)

        return () => {
            document.removeEventListener('keydown', handleKeyDown)
            trigger?.focus()
        }
    }, [open])

    return (
        <>
            <button
                ref={triggerRef}
                type="button"
                className="rating-info-button"
                aria-label="Как работает рейтинг"
                aria-haspopup="dialog"
                aria-expanded={open}
                onClick={() => setOpen(true)}
            >
                <svg
                    width="22"
                    height="22"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    aria-hidden="true"
                >
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 11v5" />
                    <circle cx="12" cy="7.5" r=".8" fill="currentColor" />
                </svg>
            </button>

            {open && (
                <div
                    className="modal-backdrop"
                    role="presentation"
                    onClick={(event) => {
                        if (event.target === event.currentTarget) {
                            setOpen(false)
                        }
                    }}
                >
                    <section
                        className="rating-info-modal"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby={titleId}
                    >
                        <div className="section-title">
                            <h2 id={titleId}>Как работает рейтинг</h2>

                            <button
                                ref={closeRef}
                                type="button"
                                className="modal-close"
                                aria-label="Закрыть"
                                onClick={() => setOpen(false)}
                            >
                                ×
                            </button>
                        </div>

                        <p className="rating-info-intro">
                            PingStats использует рейтинговую систему Glicko-2
                            для оценки уровня игроков.
                        </p>

                        <div className="rating-info-rules">
                            {ratingRules.map((rule) => (
                                <div key={rule.title}>
                                    <h3>{rule.title}</h3>
                                    <p>{rule.description}</p>
                                </div>
                            ))}
                        </div>

                        <button
                            type="button"
                            className="primary-button"
                            onClick={() => setOpen(false)}
                        >
                            Понятно
                        </button>
                    </section>
                </div>
            )}
        </>
    )
}