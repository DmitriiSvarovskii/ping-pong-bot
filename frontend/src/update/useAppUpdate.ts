import { useEffect, useState } from 'react'

import { APP_BUILD_ID, AppUpdateController } from './appUpdate'

export function useAppUpdate(): string | null {
  const [availableBuildId, setAvailableBuildId] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AppUpdateController({
      currentBuildId: APP_BUILD_ID,
      onUpdate: setAvailableBuildId,
    })
    controller.start()
    return () => controller.stop()
  }, [])

  return availableBuildId
}
