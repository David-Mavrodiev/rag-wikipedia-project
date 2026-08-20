import { useEffect, useState } from 'react'

interface DatasetMetrics {
  'recall@5'?: number
  'precision@5'?: number
  mrr?: number
  refusal_accuracy?: number
  false_accept_rate?: number
  answerable_refusal_rate?: number
}

interface QualityState {
  status: string
  updated_at?: string | null
  reason?: string
  metrics?: Record<string, DatasetMetrics>
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

function formatMetric(value: number | undefined): string {
  if (value === undefined) {
    return '-'
  }
  return `${(value * 100).toFixed(1)}%`
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ')
}

export default function QualityPanel() {
  const [quality, setQuality] = useState<QualityState | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Deployments refuse /quality/audit at the edge: it is CPU-heavy and mutates
  // the reported quality state, so it is not a public operation. Discover that
  // from the response rather than from a build flag, so the same bundle behaves
  // correctly whether it is served by nginx or by the dev server.
  const [auditUnavailable, setAuditUnavailable] = useState(false)

  const loadQuality = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`${API_BASE}/quality`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      setQuality(await response.json())
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const runAudit = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`${API_BASE}/quality/audit`, { method: 'POST' })
      if (response.status === 403 || response.status === 404 || response.status === 405) {
        setAuditUnavailable(true)
        setError('Audits are not available here. Run `make eval-audit` from the CLI.')
        return
      }
      if (response.status === 409) {
        setError('An audit is already running. Try again shortly.')
        return
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      setQuality(await response.json())
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadQuality()
  }, [])

  const datasets = quality?.metrics ? Object.entries(quality.metrics) : []

  return (
    <section className="quality-panel" aria-labelledby="quality-title">
      <div className="quality-header">
        <div>
          <h2 id="quality-title">Evaluation Metrics</h2>
          <p className={`quality-status quality-status-${quality?.status || 'unknown'}`}>
            {statusLabel(quality?.status || 'unknown')}
          </p>
        </div>
        <button type="button" onClick={runAudit} disabled={loading || auditUnavailable}>
          {loading ? 'Checking...' : 'Run audit'}
        </button>
      </div>

      {error && <p className="quality-error">Quality check unavailable: {error}</p>}

      {datasets.length > 0 ? (
        <div className="quality-table-wrap">
          <table className="quality-table">
            <thead>
              <tr>
                <th>Set</th>
                <th>Recall@5</th>
                <th>Precision@5</th>
                <th>MRR</th>
                <th>Refusal</th>
                <th>False Accept</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map(([name, metrics]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{formatMetric(metrics['recall@5'])}</td>
                  <td>{formatMetric(metrics['precision@5'])}</td>
                  <td>{formatMetric(metrics.mrr)}</td>
                  <td>{formatMetric(metrics.refusal_accuracy)}</td>
                  <td>{formatMetric(metrics.false_accept_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="quality-empty">
          No audit has been run in this API process. Use Run audit to refresh the live metrics.
        </p>
      )}

      {quality?.updated_at && (
        <p className="quality-updated">Updated {new Date(quality.updated_at).toLocaleString()}</p>
      )}
      {quality?.reason && <p className="quality-reason">{quality.reason}</p>}
    </section>
  )
}
