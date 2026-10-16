import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { useWakeOnFocus } from './useWakeOnFocus'

afterEach(() => {
  vi.restoreAllMocks()
})

test('nothing is requested until the visitor shows intent', () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useWakeOnFocus(''))
  expect(result.current.wakeState).toBe('idle')
  expect(fetchMock).not.toHaveBeenCalled()
})

test('focusing the box knocks on /health once, however many times it is focused', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true })
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useWakeOnFocus('https://api.example'))

  act(() => result.current.wake())
  act(() => result.current.wake())
  act(() => result.current.wake())

  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock).toHaveBeenCalledWith('https://api.example/health', { method: 'GET' })
  await waitFor(() => expect(result.current.wakeState).toBe('awake'))
})

test('a failed wake-up is silent: the question itself will report a real outage', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
  const { result } = renderHook(() => useWakeOnFocus(''))

  act(() => result.current.wake())

  await waitFor(() => expect(result.current.wakeState).toBe('idle'))
})
