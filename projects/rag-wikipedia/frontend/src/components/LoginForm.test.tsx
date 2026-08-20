import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import LoginForm from './LoginForm'

const props = {
  onSubmit: vi.fn(),
  onSso: vi.fn(),
  onSwitch: vi.fn(),
  loading: false,
  error: null,
}

test('renders identifier, password and submit', () => {
  render(<LoginForm {...props} />)
  expect(screen.getByTestId('login-identifier')).toBeInTheDocument()
  expect(screen.getByTestId('login-password')).toBeInTheDocument()
  expect(screen.getByTestId('login-submit')).toBeInTheDocument()
})

test('masks the password field', () => {
  render(<LoginForm {...props} />)
  expect(screen.getByTestId('login-password')).toHaveAttribute('type', 'password')
})

test('links to the provider handshake, not to the provider directly', () => {
  render(<LoginForm {...props} />)
  expect(screen.getByTestId('oauth-google')).toHaveAttribute(
    'href',
    '/auth/oauth/google/authorize',
  )
  expect(screen.getByTestId('oauth-github')).toHaveAttribute(
    'href',
    '/auth/oauth/github/authorize',
  )
})

test('submits a trimmed identifier and the password verbatim', () => {
  const onSubmit = vi.fn()
  render(<LoginForm {...props} onSubmit={onSubmit} />)
  fireEvent.change(screen.getByTestId('login-identifier'), { target: { value: '  tester  ' } })
  fireEvent.change(screen.getByTestId('login-password'), { target: { value: ' pa ss ' } })
  fireEvent.submit(screen.getByTestId('login-form'))
  expect(onSubmit).toHaveBeenCalledWith('tester', ' pa ss ')
})

test('does not submit when a field is empty', () => {
  const onSubmit = vi.fn()
  render(<LoginForm {...props} onSubmit={onSubmit} />)
  fireEvent.change(screen.getByTestId('login-identifier'), { target: { value: 'tester' } })
  fireEvent.submit(screen.getByTestId('login-form'))
  expect(onSubmit).not.toHaveBeenCalled()
})

test('switches to registration', () => {
  const onSwitch = vi.fn()
  render(<LoginForm {...props} onSwitch={onSwitch} />)
  fireEvent.click(screen.getByTestId('show-register'))
  expect(onSwitch).toHaveBeenCalled()
})

test('starts single sign-on with a trimmed company address', () => {
  const onSso = vi.fn()
  render(<LoginForm {...props} onSso={onSso} />)
  fireEvent.change(screen.getByTestId('sso-email'), { target: { value: ' alice@acme.example ' } })
  fireEvent.click(screen.getByTestId('sso-submit'))
  expect(onSso).toHaveBeenCalledWith('alice@acme.example')
})

test('cannot start single sign-on without an address', () => {
  render(<LoginForm {...props} />)
  expect(screen.getByTestId('sso-submit')).toBeDisabled()
})

test('disables the form while signing in', () => {
  render(<LoginForm {...props} loading={true} />)
  expect(screen.getByTestId('login-identifier')).toBeDisabled()
  expect(screen.getByTestId('login-password')).toBeDisabled()
  expect(screen.getByTestId('login-submit')).toBeDisabled()
})

test('shows an error message', () => {
  render(<LoginForm {...props} error="Incorrect email/username or password" />)
  expect(screen.getByTestId('login-error')).toHaveTextContent(
    'Incorrect email/username or password',
  )
})
