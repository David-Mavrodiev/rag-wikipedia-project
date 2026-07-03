import { useState } from 'react'
import AnswerView from './components/AnswerView'
import CitationList from './components/CitationList'
import QueryBox from './components/QueryBox'

export interface Citation {
  index: number
  title: string
  source_id: string
  excerpt: string
}

export interface QueryResult {
  answer: string
  citations: Citation[]
}

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export default function App() {
  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      <QueryBox onSubmit={handleQuery} loading={loading} />
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <AnswerView answer={result.answer} />
          <CitationList citations={result.citations} />
        </>
      )}
    </div>
  )
}
