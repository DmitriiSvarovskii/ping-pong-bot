import { useEffect, useRef } from 'react'

export function useTelegramBackButton(onBack: () => void, enabled = true) {
  const onBackRef = useRef(onBack)

  useEffect(() => {
    onBackRef.current = onBack
  }, [onBack])

  useEffect(() => {
    const backButton = window.Telegram?.WebApp.BackButton
    if (!enabled || !backButton) return
    const handleBack = () => onBackRef.current()

    backButton.onClick(handleBack)
    backButton.show()

    return () => {
      backButton.offClick(handleBack)
      backButton.hide()
    }
  }, [enabled])
}
