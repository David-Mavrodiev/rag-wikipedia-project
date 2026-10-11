import { useState } from 'react'
import AnswerView from './components/AnswerView'
import CitationList from './components/CitationList'
import QualityPanel from './components/QualityPanel'
import QueryBox from './components/QueryBox'
import { useWakeOnFocus } from './useWakeOnFocus'

export interface Citation {
  index: number
  title: string
  source_id: string
  excerpt: string
}

export interface QueryResult {
  answer: string
  citations: Citation[]
  refused?: boolean
  cited?: boolean
}

// strip trailing slashes so a base ending in "/" doesn't produce "//query"
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

export default function App() {
  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { wakeState, wake } = useWakeOnFocus(API_BASE)

  const handleQuery = async (question: string) => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const response = await fetch(`${API_BASE}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data: QueryResult = await response.json()
      setResult(data)
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app">
      <h1>RAG Wikipedia</h1>
      <QualityPanel />
      <QueryBox onSubmit={handleQuery} loading={loading} onIntent={wake} />
      {wakeState === 'waking' && !result && (
        <p className="waking" data-testid="waking-note">
          Waking the GPU - it sleeps when nobody is asking, so the first answer takes
          longer than the ones after it.
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <AnswerView answer={result.answer} refused={result.refused} cited={result.cited} />
          <CitationList citations={result.citations} />
        </>
      )}
    </div>
  )
}
