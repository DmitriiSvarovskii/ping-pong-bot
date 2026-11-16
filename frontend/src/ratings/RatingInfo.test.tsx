import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { RatingInfo } from './RatingInfo'
import { RatingSummary } from './RatingSummary'

describe('RatingInfo', () => {
    it('opens the rating information dialog', async () => {
        render(<RatingInfo />)

        const trigger = screen.getByRole('button', {
            name: 'Как работает рейтинг',
        })

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        expect(trigger).toHaveAttribute('aria-expanded', 'false')

        await userEvent.click(trigger)

        const dialog = screen.getByRole('dialog', {
            name: 'Как работает рейтинг',
        })

        expect(dialog).toBeInTheDocument()
        expect(trigger).toHaveAttribute('aria-expanded', 'true')
        expect(dialog).toHaveTextContent('1500')
        expect(dialog).toHaveTextContent('Glicko-2')

        for (const title of [
            'Начальный рейтинг',
            'Результаты матчей',
            'Обновление рейтинга',
            'Надёжность рейтинга',
            'Перерывы в игре',
            'Товарищеские матчи',
        ]) {
            expect(within(dialog).getByRole('heading', { name: title }))
                .toBeInTheDocument()
        }
    })

    it('closes the dialog and restores focus after confirmation', async () => {
        render(<RatingInfo />)

        const trigger = screen.getByRole('button', {
            name: 'Как работает рейтинг',
        })

        await userEvent.click(trigger)
        await userEvent.click(screen.getByRole('button', { name: 'Понятно' }))

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        expect(trigger).toHaveAttribute('aria-expanded', 'false')
        expect(trigger).toHaveFocus()
    })

    it('closes the dialog using the close button', async () => {
        render(<RatingInfo />)

        await userEvent.click(
            screen.getByRole('button', { name: 'Как работает рейтинг' }),
        )

        await userEvent.click(
            screen.getByRole('button', { name: 'Закрыть' }),
        )

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('closes the dialog on Escape', async () => {
        render(<RatingInfo />)

        const trigger = screen.getByRole('button', {
            name: 'Как работает рейтинг',
        })

        await userEvent.click(trigger)
        await userEvent.keyboard('{Escape}')

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        expect(trigger).toHaveFocus()
    })

    it('closes only when clicking outside the dialog', async () => {
        render(<RatingInfo />)

        await userEvent.click(
            screen.getByRole('button', { name: 'Как работает рейтинг' }),
        )

        const dialog = screen.getByRole('dialog')

        await userEvent.click(
            within(dialog).getByText('Начальный рейтинг'),
        )

        expect(dialog).toBeInTheDocument()

        const backdrop = dialog.parentElement

        if (!backdrop) {
            throw new Error('Expected a modal backdrop')
        }

        await userEvent.click(backdrop)

        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('provides rating information before the first rating is published', async () => {
        render(<RatingSummary missing />)

        await userEvent.click(
            screen.getByRole('button', { name: 'Как работает рейтинг' }),
        )

        expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    it('provides rating information even when the rating request fails', async () => {
        render(<RatingSummary error />)

        await userEvent.click(
            screen.getByRole('button', { name: 'Как работает рейтинг' }),
        )

        expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
})