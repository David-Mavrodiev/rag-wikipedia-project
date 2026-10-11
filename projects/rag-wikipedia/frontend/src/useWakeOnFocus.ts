import { useCallback, useRef, useState } from 'react'

/**
 * Start the server waking up when the visitor shows intent, not when the page loads.
 *
 * The API is a scale-to-zero GPU container: idle costs nothing, and the first
 * request after an idle period pays a cold start. Waking on page load would be
 * smoother, but every passer-by would spend GPU seconds. Focusing the question
 * box is the cheapest honest signal that someone means to ask something, and it
 * buys the seconds they spend typing.
 *
 * `GET /health` answers while the models are still loading, so it is the right
 * thing to knock on: it returns as soon as the container exists.
 */
export type WakeState = 'idle' | 'waking' | 'awake'

export function useWakeOnFocus(apiBase: string) {
  const [state, setState] = useState<WakeState>('idle')
  // A ref, not state: a second focus must not fire a second request, and must
  // not wait for a re-render to know that.
  const started = useRef(false)

  const wake = useCallback(() => {
    if (started.current) return
    started.current = true
    setState('waking')
    fetch(`${apiBase}/health`, { method: 'GET' })
      .then(() => setState('awake'))
      // A failed wake-up is not an error the visitor should see: the question
      // they are about to ask will report it properly if the server is really down.
      .catch(() => setState('idle'))
  }, [apiBase])

  return { wakeState: state, wake }
}
