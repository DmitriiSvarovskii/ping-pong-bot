import { render, screen } from '@testing-library/react'

import { ProfileCard } from './ProfileCard'

describe('ProfileCard', () => {
  it('renders the backend profile without relying on initDataUnsafe', () => {
    render(<ProfileCard user={{ id: 'id', first_name: 'Анна', last_name: 'Смирнова', username: 'anna', photo_url: null, created_at: '2026-01-01T00:00:00Z', last_active_at: null }} />)
    expect(screen.getByText('Анна Смирнова')).toBeInTheDocument()
    expect(screen.getByText('@anna')).toBeInTheDocument()
  })
})
