import { useState } from 'react'
import type { Citation } from '../App'

interface Props {
  citations: Citation[]
}

export default function CitationList({ citations }: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  if (citations.length === 0) {
    return null
  }

  const toggle = (index: number) => {
    setExpanded((previous) => {
      const next = new Set(previous)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  return (
    <div data-testid="citation-list">
      <h3>Sources</h3>
      {citations.map((citation) => (
        <div
          key={citation.index}
          style={{ marginBottom: '0.5rem', border: '1px solid #ddd', padding: '0.5rem' }}
        >
          <button onClick={() => toggle(citation.index)} data-testid={`citation-toggle-${citation.index}`}>
            [{citation.index}] {citation.title}
          </button>
          {expanded.has(citation.index) && (
            <p
              data-testid={`citation-excerpt-${citation.index}`}
              style={{ marginTop: '0.5rem', fontSize: '0.9em' }}
            >
              {citation.excerpt}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
