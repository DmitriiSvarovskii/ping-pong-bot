import { useEffect, useRef } from 'react'

export function useTelegramBackButton(onBack: () => void, enabled = true) {
  const onBackRef = useRef(onBack)

  useEffect(() => {
    onBackRef.current = onBack
  }, [onBack])

  useEffect(() => {
    const backButton = window.Telegram?.WebApp.BackButton
    if (!backButton) return
    if (!enabled) {
      backButton.hide()
      return
    }
    const handleBack = () => onBackRef.current()

    backButton.onClick(handleBack)
    backButton.show()

    return () => {
      backButton.offClick(handleBack)
    }
  }, [enabled])

  useEffect(() => () => {
    window.Telegram?.WebApp.BackButton?.hide()
  }, [])
}
