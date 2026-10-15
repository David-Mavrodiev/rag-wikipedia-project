import { render, screen } from '@testing-library/react'
import AnswerView from './AnswerView'

test('displays answer text', () => {
  render(<AnswerView answer="Python is a language [1]." />)
  expect(screen.getByTestId('answer-view')).toHaveTextContent('Python is a language')
})

test('an uncited answer is shown with a note that it cannot be checked', () => {
  render(<AnswerView answer="Biotite is a mica [n]." cited={false} />)
  expect(screen.getByTestId('answer-view')).toHaveTextContent('Biotite is a mica')
  expect(screen.getByTestId('uncited-note')).toBeInTheDocument()
})

test('a cited answer and a refusal carry no note', () => {
  const { rerender } = render(<AnswerView answer="Python [1]." cited={true} />)
  expect(screen.queryByTestId('uncited-note')).toBeNull()
  rerender(<AnswerView answer="I don't know based on the provided context." refused={true} cited={false} />)
  expect(screen.queryByTestId('uncited-note')).toBeNull()
})
