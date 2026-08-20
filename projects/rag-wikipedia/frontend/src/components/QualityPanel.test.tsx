import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import QualityPanel from './QualityPanel'

const qualityPayload = {
  status: 'healthy',
  updated_at: '2026-08-19T12:00:00Z',
  reason: 'Audit passed.',
  metrics: {
    golden: {
      'recall@5': 1,
      'precision@5': 0.735,
      mrr: 0.9646,
      refusal_accuracy: 1,
      false_accept_rate: 0,
    },
    holdout: {
      'recall@5': 1,
      'precision@5': 0.9333,
      mrr: 1,
      refusal_accuracy: 1,
      false_accept_rate: 0,
    },
  },
}

beforeEach(() => {
  vi.restoreAllMocks()
})

test('renders quality metrics from the API', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    json: async () => qualityPayload,
  } as Response)

  render(<QualityPanel />)

  expect(await screen.findByText('Evaluation Metrics')).toBeInTheDocument()
  expect(await screen.findByText('healthy')).toBeInTheDocument()
  expect(screen.getByText('golden')).toBeInTheDocument()
  expect(screen.getByText('73.5%')).toBeInTheDocument()
  expect(screen.getByText('holdout')).toBeInTheDocument()
})

test('runs audit and refreshes metrics', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ status: 'unknown', metrics: {} }),
  } as Response)
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => qualityPayload,
  } as Response)

  render(<QualityPanel />)

  fireEvent.click(await screen.findByRole('button', { name: 'Run audit' }))

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/quality/audit', { method: 'POST' }))
  expect(await screen.findByText('golden')).toBeInTheDocument()
})

test('disables the audit button when the deployment refuses audits', async () => {
  // nginx returns 403 for /quality/audit in deployed stacks: it is CPU-heavy
  // and mutates the reported quality state, so it is not a public operation.
  const fetchMock = vi.spyOn(globalThis, 'fetch')
  fetchMock.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => qualityPayload,
  } as Response)
  fetchMock.mockResolvedValueOnce({ ok: false, status: 403 } as Response)

  render(<QualityPanel />)

  fireEvent.click(await screen.findByRole('button', { name: 'Run audit' }))

  expect(await screen.findByText(/Run `make eval-audit` from the CLI/)).toBeInTheDocument()
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Run audit' })).toBeDisabled(),
  )
})

test('reports a concurrent audit without disabling the button', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
  fetchMock.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => qualityPayload,
  } as Response)
  fetchMock.mockResolvedValueOnce({ ok: false, status: 409 } as Response)

  render(<QualityPanel />)

  fireEvent.click(await screen.findByRole('button', { name: 'Run audit' }))

  expect(await screen.findByText(/already running/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Run audit' })).toBeEnabled()
})
