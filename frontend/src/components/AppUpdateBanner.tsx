import { useState } from 'react'

import { useAuth } from '../auth/AuthProvider'
import { hasUnsavedResultDraft } from '../pages/resultDraft'
import { useAppUpdate } from '../update/useAppUpdate'

export function AppUpdateBanner({
  reload = reloadApplication,
}: {
  reload?: () => void
}) {
  const availableBuildId = useAppUpdate()
  const { user } = useAuth()
  const [confirming, setConfirming] = useState(false)

  if (!availableBuildId) return null

  const requestReload = () => {
    if (user && hasUnsavedResultDraft(user.id)) {
      setConfirming(true)
      return
    }
    reload()
  }

  return (
    <aside className="app-update-banner" role="status" aria-live="polite">
      {confirming ? (
        <>
          <p>Черновик результата сохранён и восстановится после обновления, если матч ещё актуален.</p>
          <div className="app-update-actions">
            <button className="quiet-button" onClick={() => setConfirming(false)}>Отмена</button>
            <button className="primary-button" onClick={reload}>Обновить</button>
          </div>
        </>
      ) : (
        <>
          <p>Доступна новая версия приложения</p>
          <button className="primary-button" onClick={requestReload}>Обновить</button>
        </>
      )}
    </aside>
  )
}

function reloadApplication(): void {
  window.location.reload()
}
