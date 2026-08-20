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
  // The marker is its own element, spaced by flex gap rather than a text node,
  // so the label is asserted in parts.
  expect(screen.getByTestId('citation-toggle-1')).toHaveTextContent('[1]')
  expect(screen.getByTestId('citation-toggle-1')).toHaveTextContent('Python')
  expect(screen.getByTestId('citation-toggle-2')).toHaveTextContent('[2]')
  expect(screen.getByTestId('citation-toggle-2')).toHaveTextContent('ML')
})

test('expands citation on click', () => {
  render(<CitationList citations={citations} />)
  expect(screen.queryByTestId('citation-excerpt-1')).not.toBeInTheDocument()
  fireEvent.click(screen.getByTestId('citation-toggle-1'))
  expect(screen.getByTestId('citation-excerpt-1')).toHaveTextContent('Python is a language.')
})
