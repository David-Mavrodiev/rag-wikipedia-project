import { useState } from 'react'

interface Props {
  onSubmit: (question: string) => void
  loading: boolean
  /** Called when the visitor first shows intent, so a sleeping server can start waking. */
  onIntent?: () => void
}

export default function QueryBox({ onSubmit, loading, onIntent }: Props) {
  const [value, setValue] = useState('')

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (value.trim()) {
      onSubmit(value.trim())
    }
  }

  return (
    <form onSubmit={handleSubmit} data-testid="query-form">
      <input
        type="text"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onFocus={onIntent}
        placeholder="Ask a question about Wikipedia..."
        disabled={loading}
        data-testid="query-input"
        style={{ width: '70%', padding: '0.5rem' }}
      />
      <button type="submit" disabled={loading || !value.trim()} data-testid="query-submit">
        {loading ? 'Loading…' : 'Ask'}
      </button>
    </form>
  )
}
