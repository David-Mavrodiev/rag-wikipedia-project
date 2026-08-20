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
