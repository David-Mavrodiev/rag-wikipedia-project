import { ClerkProvider, SignInButton, UserButton, useAuth, useUser } from '@clerk/react'
import type { ReactNode } from 'react'

/**
 * Clerk, as JobHack uses it - with one difference the architecture forces.
 *
 * JobHack is a Next.js app: the browser never calls the model service, server
 * routes do, holding a secret the browser never sees. This is a static page
 * calling FastAPI directly, so a sign-in button alone would decorate an open
 * API. The session token is therefore SENT to the API and VERIFIED there
 * (backend/providers/clerk.py), and signing in is optional: a visitor who never
 * signs in still gets answers, which is what a demo link is for.
 *
 * Without a publishable key the whole thing is inert: no provider, no buttons,
 * and `useSessionToken` returns null. A missing key must not stop a public demo
 * from answering questions.
 *
 * `@clerk/react` v6 (core 3) has no <SignedIn>/<SignedOut>: the signed-in state
 * comes from useUser(), which is why the control below is a component of its
 * own - it may only call hooks INSIDE the provider.
 */
const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined

export const clerkConfigured = Boolean(PUBLISHABLE_KEY)

export function AuthProvider({ children }: { children: ReactNode }) {
  if (!clerkConfigured) return <>{children}</>
  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY!} afterSignOutUrl="/">
      {children}
    </ClerkProvider>
  )
}

function ClerkControls() {
  const { isSignedIn } = useUser()
  if (isSignedIn) return <UserButton />
  return (
    <SignInButton mode="modal">
      <button type="button" className="link-button">
        Sign in
      </button>
    </SignInButton>
  )
}

/** The header control: sign in, or the account button once signed in. */
export function AuthControls() {
  if (!clerkConfigured) return null
  return (
    <span className="auth-controls" data-testid="auth-controls">
      <ClerkControls />
    </span>
  )
}

/**
 * A getter for the current session token, or null when Clerk is not configured
 * or nobody is signed in. Returning null rather than throwing is deliberate:
 * the caller sends the header only when there is one, and anonymous requests
 * are a supported case, not a failure.
 *
 * The hook call is conditional on a MODULE CONSTANT, not on state: the branch
 * is decided once per build, so hook order never changes between renders.
 * Calling useAuth() without a provider would throw, which is the thing being
 * avoided here.
 */
export function useSessionToken(): () => Promise<string | null> {
  if (!clerkConfigured) return async () => null
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { getToken } = useAuth()
  return async () => {
    try {
      return await getToken()
    } catch {
      return null
    }
  }
}
