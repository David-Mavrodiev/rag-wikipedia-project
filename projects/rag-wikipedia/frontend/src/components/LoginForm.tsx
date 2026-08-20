import { useState } from 'react'
import OAuthButtons from './OAuthButtons'

interface Props {
  onSubmit: (identifier: string, password: string) => void
  onSso: (email: string) => void
  onSwitch: () => void
  loading: boolean
  error: string | null
}

export default function LoginForm({ onSubmit, onSso, onSwitch, loading, error }: Props) {
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [ssoEmail, setSsoEmail] = useState('')

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    // the password is passed through untrimmed — leading/trailing spaces are
    // legitimate characters in a passphrase
    if (identifier.trim() && password) {
      onSubmit(identifier.trim(), password)
    }
  }

  return (
    <form className="card" onSubmit={handleSubmit} data-testid="login-form">
      <h2>Sign in</h2>
      {error && (
        <p className="error" data-testid="login-error">
          {error}
        </p>
      )}
      <div className="field">
        <label htmlFor="login-identifier">Email or username</label>
        <input
          className="input"
          id="login-identifier"
          type="text"
          value={identifier}
          onChange={(event) => setIdentifier(event.target.value)}
          autoComplete="username"
          disabled={loading}
          data-testid="login-identifier"
        />
      </div>
      <div className="field">
        <label htmlFor="login-password">Password</label>
        <input
          className="input"
          id="login-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          disabled={loading}
          data-testid="login-password"
        />
      </div>
      <button
        className="button block"
        type="submit"
        disabled={loading || !identifier.trim() || !password}
        data-testid="login-submit"
      >
        {loading ? 'Signing in…' : 'Sign in'}
      </button>
      <OAuthButtons disabled={loading} />
      <div className="sso">
        <h3>Single sign-on</h3>
        {/* The company domain decides which identity provider handles this. */}
        <div className="inrow">
          <input
            className="input"
            type="email"
            value={ssoEmail}
            onChange={(event) => setSsoEmail(event.target.value)}
            placeholder="you@company.com"
            autoComplete="email"
            disabled={loading}
            data-testid="sso-email"
          />
          <button
            className="button-secondary"
            type="button"
            onClick={() => onSso(ssoEmail.trim())}
            disabled={loading || !ssoEmail.trim()}
            data-testid="sso-submit"
          >
            Continue
          </button>
        </div>
      </div>
      <p className="switch">
        No account yet?{' '}
        <button
          className="button-link"
          type="button"
          onClick={onSwitch}
          data-testid="show-register"
        >
          Create one
        </button>
      </p>
    </form>
  )
}
