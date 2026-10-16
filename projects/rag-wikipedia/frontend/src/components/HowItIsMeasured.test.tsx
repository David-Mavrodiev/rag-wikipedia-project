import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import HowItIsMeasured from './HowItIsMeasured'

test('it explains both measurements, not just the flattering one', () => {
  render(<HowItIsMeasured />)
  expect(screen.getByText(/Live check/)).toBeInTheDocument()
  expect(screen.getByText(/Published measurement/)).toBeInTheDocument()
})

test('the failing numbers are on the page, not hidden behind a link', () => {
  render(<HowItIsMeasured />)
  // The gate's false-accept rate is the worst number this project publishes.
  expect(screen.getByText(/0.800 → 0.150/)).toBeInTheDocument()
  expect(screen.getByText(/0.150 \/ 0.350/)).toBeInTheDocument()
})

test('it says the evaluator was measured, in BOTH directions', () => {
  // The page used to report only the over-flagging half. The blind split said
  // the opposite, and a page that keeps the flattering half of a measurement is
  // the thing this section exists to argue against.
  render(<HowItIsMeasured />)
  expect(screen.getByText(/four of five of its alarms were false/)).toBeInTheDocument()
  expect(screen.getByText(/slept through four of five real problems/)).toBeInTheDocument()
  expect(screen.getByText(/not a floor/)).toBeInTheDocument()
})

test('the full report is linked', () => {
  render(<HowItIsMeasured />)
  const link = screen.getByRole('link', { name: /full report/i })
  expect(link).toHaveAttribute('href', expect.stringContaining('measurement-baseline.md'))
})
