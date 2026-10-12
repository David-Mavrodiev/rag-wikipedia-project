import { useState } from 'react'
import AnswerView from './components/AnswerView'
import CitationList from './components/CitationList'
import ExampleQuestions from './components/ExampleQuestions'
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

const REPO = 'https://github.com/David-Mavrodiev/rag-wikipedia-project'

export default function App() {
  const [result, setResult] = useState<QueryResult | null>(null)
  const [asked, setAsked] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { wakeState, wake } = useWakeOnFocus(API_BASE)

  const handleQuery = async (question: string) => {
    setLoading(true)
    setError(null)
    setResult(null)
    setAsked(question)
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
    <>
      <header className="site-header">
        <nav>
          <span className="brand">RAG over Wikipedia</span>
          <span className="nav-links">
            <a href={`${REPO}/blob/main/projects/rag-wikipedia/docs/measurement-baseline.md`}>
              The numbers
            </a>
            <a href={REPO}>Source</a>
          </span>
        </nav>
      </header>

      <main>
        <h1>Ask 24,694 Wikipedia articles a question</h1>
        <p className="lede">
          Answers come only from the 87,173 retrieved passages, with the sources shown. When
          the corpus does not hold the answer, the system says so instead of inventing one -
          and how often it gets that judgement wrong is measured and published.
        </p>

        <div className="card">
          <QueryBox onSubmit={handleQuery} loading={loading} onIntent={wake} />
          {wakeState === 'waking' && !result && !loading && (
            <p className="waking" data-testid="waking-note">
              Waking the GPU - it sleeps when nobody is asking, so the first answer takes
              longer than the ones after it.
            </p>
          )}
          <ExampleQuestions onPick={handleQuery} disabled={loading} />
        </div>

        {loading && <p className="note">Searching the corpus…</p>}
        {error && <p className="error">Something went wrong: {error}</p>}

        {result && (
          <div className="card">
            {asked && <h2>{asked}</h2>}
            <AnswerView answer={result.answer} refused={result.refused} cited={result.cited} />
            <CitationList citations={result.citations} />
          </div>
        )}

        <QualityPanel />
      </main>

      <footer className="site-footer">
        <p>
          Built with Ollama, llama3.2:3b, bge-small and Qdrant - self-hosted, no model
          provider involved. The GPU scales to zero between questions.
        </p>
        <p>
          Retrieval, refusal and groundedness are measured on held-out questions, and the
          failing numbers are published with the passing ones.
        </p>
      </footer>
    </>
  )
}
