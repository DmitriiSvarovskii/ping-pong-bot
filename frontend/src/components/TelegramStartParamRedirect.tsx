import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

import { getSessionStartParam } from '../auth/telegram'

export function TelegramStartParamRedirect() {
  const navigate = useNavigate()
  const handled = useRef(false)
  useEffect(() => {
    if (handled.current) return
    handled.current = true
    const sessionId = getSessionStartParam()
    if (sessionId) void navigate(`/games/${sessionId}/invite`, { replace: true })
  }, [navigate])
  return null
}
