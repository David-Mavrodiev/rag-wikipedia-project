// strip trailing slashes so a base ending in "/" doesn't produce "//query"
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

export interface User {
  id: string
  email: string
  username: string
  created_at: string
  has_password: boolean
  providers: string[]
  role: string
  is_active: boolean
  organization: string | null
}

export type OAuthProvider = 'google' | 'github'

// The session is an HttpOnly cookie, so no token is ever readable from here.
// Every call only has to opt into sending credentials.
const CREDENTIALS: RequestInit = { credentials: 'include' }

export async function register(
  email: string,
  username: string,
  password: string,
): Promise<User> {
  return postJson<User>('/auth/register', { email, username, password }, 'Registration failed')
}

export async function login(identifier: string, password: string): Promise<User> {
  return postJson<User>('/auth/login', { identifier, password }, 'Sign-in failed')
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', ...CREDENTIALS })
}

/** Resolve the signed-in user, or null when the cookie is missing or expired. */
export async function fetchCurrentUser(): Promise<User | null> {
  const response = await fetch(`${API_BASE}/auth/me`, CREDENTIALS)
  if (response.status === 401) {
    return null
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return (await response.json()) as User
}

/** Full-page navigation target that starts the provider handshake. */
export function oauthUrl(provider: OAuthProvider): string {
  return `${API_BASE}/auth/oauth/${provider}/authorize`
}

/**
 * Home-realm discovery: the email domain decides which enterprise IdP handles
 * the sign-in. Returns the URL to send the browser to.
 */
export async function startSso(email: string): Promise<string> {
  const { authorize_url } = await postJson<{ authorize_url: string }>(
    '/auth/sso/start',
    { email },
    'Single sign-on is unavailable',
  )
  return authorize_url
}

/** Hand the browser to the provider. Wrapped so tests can stub the navigation. */
export function navigateTo(url: string): void {
  window.location.assign(url)
}

async function postJson<T>(path: string, body: unknown, fallback: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...CREDENTIALS,
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response, fallback))
  }
  return (await response.json()) as T
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const { detail } = await response.json()
    if (typeof detail === 'string') {
      return detail
    }
    // FastAPI reports validation failures as a list of {loc, msg} entries.
    if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') {
      return detail[0].msg
    }
  } catch {
    // no JSON body — fall through
  }
  return fallback
}
