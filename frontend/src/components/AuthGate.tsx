import type { ReactNode } from 'react'

import { useAuth } from '../auth/AuthProvider'
import { PaddleIcon } from './icons'

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth()
  if (auth.status === 'authenticated') return children

  return (
    <main className="auth-screen">
      <div className="brand-mark"><PaddleIcon /></div>
      <p className="eyebrow">PINGSTATS</p>
      {auth.status === 'loading' ? (
        <><h1>Подключаемся</h1><p>Проверяем Telegram и загружаем профиль…</p><div className="loader" aria-label="Загрузка" /></>
      ) : auth.status === 'development-login' ? (
        <><h1>Локальная разработка</h1><p>Telegram initData отсутствует. Используйте фиксированного development-пользователя.</p><button className="primary-button" onClick={() => auth.loginForDevelopment()}>Войти для разработки</button></>
      ) : (
        <><h1>Не удалось войти</h1><p>{auth.errorMessage}</p><button className="primary-button" onClick={() => auth.retry()}>Попробовать снова</button></>
      )}
    </main>
  )
}
