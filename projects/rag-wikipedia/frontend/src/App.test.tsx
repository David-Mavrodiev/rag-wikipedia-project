import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import App from './App'
import { navigateTo } from './auth'

// Only the navigation is stubbed; the rest of the auth client runs for real
// against the fake API below.
vi.mock('./auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./auth')>()),
  navigateTo: vi.fn(),
}))

const USER = {
  id: 'user-1',
  email: 'tester@example.com',
  username: 'tester',
  created_at: '2026-01-01T00:00:00Z',
  has_password: true,
  providers: [] as string[],
  role: 'member',
  is_active: true,
  organization: null as string | null,
}

const fetchMock = vi.fn()
let session: typeof USER | null = null

const json = (status: number, body?: unknown) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

beforeEach(() => {
  session = null
  window.history.replaceState({}, '', '/')
  fetchMock.mockReset()
  vi.mocked(navigateTo).mockClear()
  // A miniature API. The session lives here, the way the HttpOnly cookie lives
  // in the browser — the component never handles a token itself.
  fetchMock.mockImplementation(async (url: string) => {
    const path = String(url)
    if (path.endsWith('/auth/me')) {
      return session ? json(200, session) : json(401, { detail: 'Not authenticated' })
    }
    if (path.endsWith('/auth/register')) {
      session = USER
      return json(201, USER)
    }
    if (path.endsWith('/auth/login')) {
      session = USER
      return json(200, USER)
    }
    if (path.endsWith('/auth/logout')) {
      session = null
      return json(204)
    }
    if (path.endsWith('/auth/sso/start')) {
      return json(200, { authorize_url: 'https://idp.acme.example/authorize?state=abc' })
    }
    if (path.endsWith('/query')) {
      return session
        ? json(200, { answer: 'Python is a language.', citations: [] })
        : json(401, { detail: 'Not authenticated' })
    }
    throw new Error(`unexpected request: ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})

const callTo = (path: string) =>
  fetchMock.mock.calls.find(([url]) => String(url).endsWith(path))

async function signIn() {
  fireEvent.change(await screen.findByTestId('login-identifier'), { target: { value: 'tester' } })
  fireEvent.change(screen.getByTestId('login-password'), { target: { value: 'password123' } })
  fireEvent.submit(screen.getByTestId('login-form'))
  await screen.findByTestId('query-form')
}

test('shows the sign-in form when there is no session', async () => {
  render(<App />)
  expect(await screen.findByTestId('login-form')).toBeInTheDocument()
  expect(screen.queryByTestId('query-form')).not.toBeInTheDocument()
})

test('restores an existing session on load', async () => {
  session = USER
  render(<App />)

  expect(await screen.findByTestId('query-form')).toBeInTheDocument()
  expect(screen.getByTestId('current-user')).toHaveTextContent('tester')
})

test('signs in with an email or username', async () => {
  render(<App />)
  await signIn()

  const [, init] = callTo('/auth/login')!
  expect(init.credentials).toBe('include')
  expect(JSON.parse(init.body)).toEqual({ identifier: 'tester', password: 'password123' })
})

test('registers a new account and signs it in', async () => {
  render(<App />)
  fireEvent.click(await screen.findByTestId('show-register'))
  fireEvent.change(screen.getByTestId('register-email'), {
    target: { value: 'tester@example.com' },
  })
  fireEvent.change(screen.getByTestId('register-username'), { target: { value: 'tester' } })
  fireEvent.change(screen.getByTestId('register-password'), { target: { value: 'password123' } })
  fireEvent.submit(screen.getByTestId('register-form'))

  expect(await screen.findByTestId('query-form')).toBeInTheDocument()
  expect(JSON.parse(callTo('/auth/register')![1].body)).toEqual({
    email: 'tester@example.com',
    username: 'tester',
    password: 'password123',
  })
})

test('reports a rejected sign-in without granting access', async () => {
  render(<App />)
  await screen.findByTestId('login-form')
  fetchMock.mockImplementationOnce(async () =>
    json(401, { detail: 'Incorrect email/username or password' }),
  )

  fireEvent.change(screen.getByTestId('login-identifier'), { target: { value: 'tester' } })
  fireEvent.change(screen.getByTestId('login-password'), { target: { value: 'wrong' } })
  fireEvent.submit(screen.getByTestId('login-form'))

  expect(await screen.findByTestId('login-error')).toHaveTextContent(
    'Incorrect email/username or password',
  )
  expect(screen.queryByTestId('query-form')).not.toBeInTheDocument()
})

test('sends the session cookie with each query', async () => {
  render(<App />)
  await signIn()
  fireEvent.change(screen.getByTestId('query-input'), { target: { value: 'What is Python?' } })
  fireEvent.submit(screen.getByTestId('query-form'))

  await screen.findByTestId('answer-view')
  const [url, init] = callTo('/query')!
  expect(url).toBe('/query')
  expect(init.credentials).toBe('include')
  // No Authorization header: the browser attaches the HttpOnly cookie instead.
  expect(init.headers.Authorization).toBeUndefined()
})

test('returns to sign-in when the session is rejected', async () => {
  render(<App />)
  await signIn()
  session = null // expired between requests

  fireEvent.change(screen.getByTestId('query-input'), { target: { value: 'What is Python?' } })
  fireEvent.submit(screen.getByTestId('query-form'))

  expect(await screen.findByTestId('login-error')).toHaveTextContent('session has expired')
  expect(screen.queryByTestId('query-form')).not.toBeInTheDocument()
})

test('signing out ends the session server-side', async () => {
  render(<App />)
  await signIn()
  fireEvent.click(screen.getByTestId('logout'))

  expect(await screen.findByTestId('login-form')).toBeInTheDocument()
  expect(callTo('/auth/logout')).toBeDefined()
})

test('explains a failed social sign-in reported by the callback redirect', async () => {
  window.history.replaceState({}, '', '/?auth_error=email_unverified')
  render(<App />)

  expect(await screen.findByTestId('login-error')).toHaveTextContent(
    'has not verified your email address',
  )
  // The parameter is cleared so a reload does not repeat the message.
  expect(window.location.search).toBe('')
})

test('hands the browser to the provider for a managed domain', async () => {
  render(<App />)
  await screen.findByTestId('login-form')

  fireEvent.change(screen.getByTestId('sso-email'), { target: { value: 'alice@acme.example' } })
  fireEvent.click(screen.getByTestId('sso-submit'))

  await waitFor(() =>
    expect(navigateTo).toHaveBeenCalledWith('https://idp.acme.example/authorize?state=abc'),
  )
  expect(JSON.parse(callTo('/auth/sso/start')![1].body)).toEqual({ email: 'alice@acme.example' })
})

test('explains a domain that has no single sign-on', async () => {
  render(<App />)
  await screen.findByTestId('login-form')
  fetchMock.mockImplementationOnce(async () =>
    json(404, { detail: 'No single sign-on is configured for that domain' }),
  )

  fireEvent.change(screen.getByTestId('sso-email'), { target: { value: 'someone@gmail.com' } })
  fireEvent.click(screen.getByTestId('sso-submit'))

  expect(await screen.findByTestId('login-error')).toHaveTextContent(
    'No single sign-on is configured for that domain',
  )
  expect(navigateTo).not.toHaveBeenCalled()
})

test('explains an enterprise sign-in rejected by the callback', async () => {
  window.history.replaceState({}, '', '/?auth_error=sso_domain_mismatch')
  render(<App />)

  expect(await screen.findByTestId('login-error')).toHaveTextContent(
    'outside your organization',
  )
})

test('shows the organization and role of an enterprise user', async () => {
  session = { ...USER, role: 'admin', organization: 'Acme' }
  render(<App />)

  expect(await screen.findByTestId('current-user')).toHaveTextContent('tester · Acme · admin')
})
