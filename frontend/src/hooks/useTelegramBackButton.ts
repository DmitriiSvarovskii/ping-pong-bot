import { useEffect } from 'react'

export function useTelegramBackButton(onBack: () => void, enabled = true) {
  useEffect(() => {
    const backButton = window.Telegram?.WebApp.BackButton
    if (!enabled || !backButton) return

    backButton.onClick(onBack)
    backButton.show()

    return () => {
      backButton.offClick(onBack)
      backButton.hide()
    }
  }, [enabled, onBack])
}
