import { fireEvent, render, screen } from '@testing-library/react'
import CitationList from './CitationList'

const citations = [
  { index: 1, title: 'Python', source_id: '1', excerpt: 'Python is a language.' },
  { index: 2, title: 'ML', source_id: '2', excerpt: 'ML is AI subset.' },
]

test('renders nothing when no citations', () => {
  const { container } = render(<CitationList citations={[]} />)
  expect(container).toBeEmptyDOMElement()
})

test('renders citation titles', () => {
  render(<CitationList citations={citations} />)
  expect(screen.getByText(/\[1\] Python/)).toBeInTheDocument()
  expect(screen.getByText(/\[2\] ML/)).toBeInTheDocument()
})

test('expands citation on click', () => {
  render(<CitationList citations={citations} />)
  expect(screen.queryByTestId('citation-excerpt-1')).not.toBeInTheDocument()
  fireEvent.click(screen.getByTestId('citation-toggle-1'))
  expect(screen.getByTestId('citation-excerpt-1')).toHaveTextContent('Python is a language.')
})
