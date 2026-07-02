import { render, screen } from '@testing-library/react'
import AnswerView from './AnswerView'

test('displays answer text', () => {
  render(<AnswerView answer="Python is a language [1]." />)
  expect(screen.getByTestId('answer-view')).toHaveTextContent('Python is a language')
})
