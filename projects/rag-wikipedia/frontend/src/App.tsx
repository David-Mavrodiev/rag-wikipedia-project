import { useCallback, useEffect, useState } from 'react'
import {
  API_BASE,
  fetchCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  navigateTo,
  register as registerRequest,
  startSso,
  type User,
} from './auth'
import AnswerView from './components/AnswerView'
import CitationList from './components/CitationList'
import LoginForm from './components/LoginForm'
import QueryBox from './components/QueryBox'
import RegisterForm from './components/RegisterForm'

export interface Citation {
  index: number
  title: string
  source_id: string
  excerpt: string
}

export interface QueryResult {
  answer: string
  citations: Citation[]
}

// A provider callback cannot render a message itself — it redirects here with a
// fixed code, which maps to the text below.
const AUTH_ERRORS: Record<string, string> = {
  oauth_failed: 'Social sign-in failed. Please try again.',
  email_unverified:
    'That provider has not verified your email address, so it cannot be used to sign in.',
  sso_failed: 'Single sign-on failed. Please try again.',
  sso_domain_mismatch:
    'Your identity provider returned an address outside your organization’s domains.',
  sso_no_account:
    'Your organization does not create accounts automatically. Ask an administrator to invite you.',
  account_disabled: 'This account has been deactivated.',
}

function readAuthError(): string | null {
  const code = new URLSearchParams(window.location.search).get('auth_error')
  if (!code) {
    return null
  }
  // Drop the parameter so a reload does not show the message again.
  window.history.replaceState({}, '', window.location.pathname)
  return AUTH_ERRORS[code] ?? AUTH_ERRORS.oauth_failed
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checkingSession, setCheckingSession] = useState(true)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [authError, setAuthError] = useState<string | null>(null)
  const [authLoading, setAuthLoading] = useState(false)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // The cookie is HttpOnly, so the only way to know whether this browser has
    // a live session is to ask the API.
    setAuthError(readAuthError())
    fetchCurrentUser()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setCheckingSession(false))
  }, [])

  const runAuth = useCallback(async (action: () => Promise<User>) => {
    setAuthLoading(true)
    setAuthError(null)
    try {
      setUser(await action())
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Unknown error')
    } finally {
      setAuthLoading(false)
    }
  }, [])

  const handleLogin = (identifier: string, password: string) =>
    runAuth(() => loginRequest(identifier, password))

  const handleRegister = (email: string, username: string, password: string) =>
    runAuth(() => registerRequest(email, username, password))

  const handleSso = async (email: string) => {
    setAuthLoading(true)
    setAuthError(null)
    try {
      // Leaves the app: the rest of the handshake happens at the provider and
      // comes back through the callback, so `loading` stays set on purpose.
      navigateTo(await startSso(email))
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Unknown error')
      setAuthLoading(false)
    }
  }

  const endSession = (message: string | null) => {
    setUser(null)
    setResult(null)
    setError(null)
    setMode('login')
    setAuthError(message)
  }

  const handleLogout = async () => {
    await logoutRequest()
    endSession(null)
  }

  const handleQuery = async (question: string) => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const response = await fetch(`${API_BASE}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
        // send the session cookie
        credentials: 'include',
      })
      if (response.status === 401) {
        // Expired or revoked session: send the user back to sign-in rather than
        // surfacing a bare "HTTP 401".
        endSession('Your session has expired. Please sign in again.')
        return
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data: QueryResult = await response.json()
      setResult(data)
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  if (checkingSession) {
    return (
      <div className="app">
        <Header />
        <main className="app-main narrow">
          <p className="muted" data-testid="session-loading">
            Loading…
          </p>
        </main>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="app">
        <Header />
        <main className="app-main narrow">
          {mode === 'login' ? (
            <LoginForm
              onSubmit={handleLogin}
              onSso={handleSso}
              onSwitch={() => {
                setMode('register')
                setAuthError(null)
              }}
              loading={authLoading}
              error={authError}
            />
          ) : (
            <RegisterForm
              onSubmit={handleRegister}
              onSwitch={() => {
                setMode('login')
                setAuthError(null)
              }}
              loading={authLoading}
              error={authError}
            />
          )}
        </main>
      </div>
    )
  }

  return (
    <div className="app">
      <Header>
        <span data-testid="current-user">
          {user.username}
          {user.organization ? ` · ${user.organization}` : ''}
          {user.role === 'admin' ? ' · admin' : ''}
        </span>
        <button className="button-link" type="button" onClick={handleLogout} data-testid="logout">
          Sign out
        </button>
      </Header>
      <main className="app-main">
        <QueryBox onSubmit={handleQuery} loading={loading} />
        {error && <p className="error">{error}</p>}
        {result && (
          <>
            <AnswerView answer={result.answer} />
            <CitationList citations={result.citations} />
          </>
        )}
      </main>
    </div>
  )
}

function Header({ children }: { children?: React.ReactNode }) {
  return (
    <header className="app-header">
      <h1>RAG&nbsp;Wikipedia</h1>
      <span className="sub">grounded, cited answers</span>
      {children && <div className="app-identity">{children}</div>}
    </header>
  )
}
