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
  expect(screen.getByText(/0.133 \/ 0.350/)).toBeInTheDocument()
})

test('it says the evaluator itself was measured', () => {
  render(<HowItIsMeasured />)
  expect(screen.getByText(/four of five of its alarms are false/)).toBeInTheDocument()
})

test('the full report is linked', () => {
  render(<HowItIsMeasured />)
  const link = screen.getByRole('link', { name: /full report/i })
  expect(link).toHaveAttribute('href', expect.stringContaining('measurement-baseline.md'))
})
