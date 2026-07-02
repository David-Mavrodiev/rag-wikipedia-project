import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import QueryBox from './QueryBox'

test('renders input and button', () => {
  render(<QueryBox onSubmit={vi.fn()} loading={false} />)
  expect(screen.getByTestId('query-input')).toBeInTheDocument()
  expect(screen.getByTestId('query-submit')).toBeInTheDocument()
})

test('calls onSubmit with trimmed value', () => {
  const onSubmit = vi.fn()
  render(<QueryBox onSubmit={onSubmit} loading={false} />)
  fireEvent.change(screen.getByTestId('query-input'), { target: { value: '  hello  ' } })
  fireEvent.submit(screen.getByTestId('query-form'))
  expect(onSubmit).toHaveBeenCalledWith('hello')
})

test('disables input and button when loading', () => {
  render(<QueryBox onSubmit={vi.fn()} loading={true} />)
  expect(screen.getByTestId('query-input')).toBeDisabled()
  expect(screen.getByTestId('query-submit')).toBeDisabled()
})
