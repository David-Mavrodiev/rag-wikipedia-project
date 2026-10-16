import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import ExampleQuestions from './ExampleQuestions'

test('offers the three behaviours worth seeing', () => {
  render(<ExampleQuestions onPick={vi.fn()} />)
  const buttons = screen.getAllByRole('button')
  expect(buttons).toHaveLength(3)
  // The third one is the refusal: a demo that only shows answers hides the point.
  expect(buttons[2].textContent).toContain('AIM-120 AMRAAM')
  expect(buttons[2].textContent).toContain('declines instead of inventing')
})

test('picking one asks it', () => {
  const onPick = vi.fn()
  render(<ExampleQuestions onPick={onPick} />)
  fireEvent.click(screen.getAllByRole('button')[0])
  expect(onPick).toHaveBeenCalledWith('What is the Fuji T-3?')
})

test('they are disabled while an answer is being fetched', () => {
  render(<ExampleQuestions onPick={vi.fn()} disabled />)
  screen.getAllByRole('button').forEach((button) => expect(button).toBeDisabled())
})
