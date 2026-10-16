import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { AuthControls, AuthProvider, clerkConfigured } from './auth'

// No VITE_CLERK_PUBLISHABLE_KEY in the test environment: this is the state a
// public demo runs in before any key exists, and it must be harmless.

test('without a key, Clerk is inert', () => {
  expect(clerkConfigured).toBe(false)
})

test('the provider still renders the app', () => {
  render(
    <AuthProvider>
      <p>the demo</p>
    </AuthProvider>,
  )
  expect(screen.getByText('the demo')).toBeInTheDocument()
})

test('no sign-in control is shown when there is nothing to sign in to', () => {
  render(<AuthControls />)
  expect(screen.queryByTestId('auth-controls')).toBeNull()
})
