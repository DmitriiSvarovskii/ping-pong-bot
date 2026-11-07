import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AuthProvider } from '../auth/AuthProvider'
import { AppShell } from '../components/AppShell'
import { AuthGate } from '../components/AuthGate'
import { TelegramStartParamRedirect } from '../components/TelegramStartParamRedirect'
import { CreateSessionPage } from '../pages/CreateSessionPage'
import { HomePage } from '../pages/HomePage'
import { JoinSessionPage } from '../pages/JoinSessionPage'
import { PlaceholderPage } from '../pages/PlaceholderPage'
import { ProfilePage } from '../pages/ProfilePage'
import { ResultPage } from '../pages/ResultPage'
import { SessionInvitePage } from '../pages/SessionInvitePage'
import { SessionPage } from '../pages/SessionPage'
import { SessionsPage } from '../pages/SessionsPage'

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000 } } })

export function App() {
  return <QueryClientProvider client={queryClient}><AuthProvider><AuthGate><BrowserRouter><TelegramStartParamRedirect/><Routes><Route element={<AppShell />}><Route index element={<HomePage />} /><Route path="profile" element={<ProfilePage />} /><Route path="games" element={<SessionsPage />} /><Route path="games/create" element={<CreateSessionPage />} /><Route path="games/join" element={<JoinSessionPage />} /><Route path="games/:sessionId" element={<SessionPage />} /><Route path="games/:sessionId/result" element={<ResultPage />} /><Route path="games/:sessionId/invite" element={<SessionInvitePage />} /><Route path="*" element={<PlaceholderPage title="Страница не найдена" description="Проверьте адрес или вернитесь на главную." />} /></Route></Routes></BrowserRouter></AuthGate></AuthProvider></QueryClientProvider>
}
