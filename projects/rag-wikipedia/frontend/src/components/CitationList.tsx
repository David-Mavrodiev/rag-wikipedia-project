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
    <div className="card" data-testid="citation-list">
      <h3>Sources</h3>
      <div className="cites">
        {citations.map((citation) => (
          <div className="cite" key={citation.index}>
            <button
              className="cite-toggle"
              type="button"
              onClick={() => toggle(citation.index)}
              data-testid={`citation-toggle-${citation.index}`}
            >
              <span className="idx">[{citation.index}]</span>
              {citation.title}
            </button>
            {expanded.has(citation.index) && (
              <p className="excerpt" data-testid={`citation-excerpt-${citation.index}`}>
                {citation.excerpt}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
