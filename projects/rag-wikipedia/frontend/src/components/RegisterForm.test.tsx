import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import RegisterForm from './RegisterForm'

const props = { onSubmit: vi.fn(), onSwitch: vi.fn(), loading: false, error: null }

test('renders email, username, password and submit', () => {
  render(<RegisterForm {...props} />)
  expect(screen.getByTestId('register-email')).toBeInTheDocument()
  expect(screen.getByTestId('register-username')).toBeInTheDocument()
  expect(screen.getByTestId('register-password')).toBeInTheDocument()
  expect(screen.getByTestId('register-submit')).toBeInTheDocument()
})

test('masks the password field', () => {
  render(<RegisterForm {...props} />)
  expect(screen.getByTestId('register-password')).toHaveAttribute('type', 'password')
})

test('submits trimmed identifiers and the password verbatim', () => {
  const onSubmit = vi.fn()
  render(<RegisterForm {...props} onSubmit={onSubmit} />)
  fireEvent.change(screen.getByTestId('register-email'), {
    target: { value: '  tester@example.com ' },
  })
  fireEvent.change(screen.getByTestId('register-username'), { target: { value: ' tester ' } })
  fireEvent.change(screen.getByTestId('register-password'), { target: { value: ' pa ss word ' } })
  fireEvent.submit(screen.getByTestId('register-form'))
  expect(onSubmit).toHaveBeenCalledWith('tester@example.com', 'tester', ' pa ss word ')
})

test('does not submit an incomplete form', () => {
  const onSubmit = vi.fn()
  render(<RegisterForm {...props} onSubmit={onSubmit} />)
  fireEvent.change(screen.getByTestId('register-email'), {
    target: { value: 'tester@example.com' },
  })
  fireEvent.submit(screen.getByTestId('register-form'))
  expect(onSubmit).not.toHaveBeenCalled()
})

test('switches back to sign-in', () => {
  const onSwitch = vi.fn()
  render(<RegisterForm {...props} onSwitch={onSwitch} />)
  fireEvent.click(screen.getByTestId('show-login'))
  expect(onSwitch).toHaveBeenCalled()
})

test('shows an error message', () => {
  render(<RegisterForm {...props} error="Email or username is already taken" />)
  expect(screen.getByTestId('register-error')).toHaveTextContent(
    'Email or username is already taken',
  )
})
