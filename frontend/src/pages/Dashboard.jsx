import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useBrand } from '../context/BrandContext'
import {
  getJobs,
  archiveJob,
  deleteJob,
  createScheduledPost,
  downloadJobVideo,
  getDashboardMetrics,
} from '../api'
import './Dashboard.css'

const PLATFORMS = [
  { id: 'IG', label: 'Instagram Reels' },
  { id: 'TT', label: 'TikTok' },
  { id: 'YT', label: 'YouTube Shorts' },
  { id: 'YTB', label: 'YouTube' },
]

function formatInt(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toLocaleString('pt-BR')
}

function formatDecimal(n, maxFrac = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toLocaleString('pt-BR', { maximumFractionDigits: maxFrac })
}

/** Minutos totais (número) → texto legível */
function formatMinutesTotal(minutes) {
  if (minutes == null || Number.isNaN(Number(minutes))) return '—'
  const m = Number(minutes)
  if (m < 60) {
    return `${m.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} min`
  }
  const h = Math.floor(m / 60)
  const rest = Math.round((m % 60) * 10) / 10
  return `${h} h ${rest.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} min`
}

export default function Dashboard() {
  const { brandId, factoryId, viewMode } = useBrand()
  const [activeJobs, setActiveJobs] = useState([])
  const [archivedJobs, setArchivedJobs] = useState([])
  const [tab, setTab] = useState('ativos')
  const [jobsLoading, setJobsLoading] = useState(false)
  const [metrics, setMetrics] = useState(null)
  const [metricsLoading, setMetricsLoading] = useState(true)
  const [metricsError, setMetricsError] = useState('')
  const [error, setError] = useState('')
  const [rescheduleJob, setRescheduleJob] = useState(null)
  const [reschedulePlatforms, setReschedulePlatforms] = useState([])
  const [rescheduleAt, setRescheduleAt] = useState('')
  const [rescheduling, setRescheduling] = useState(false)

  const canLoadMetrics =
    viewMode === 'factory' ? !!factoryId : !!brandId

  useEffect(() => {
    if (!canLoadMetrics) {
      setMetrics(null)
      setMetricsLoading(false)
      setMetricsError('')
      return
    }
    let cancelled = false
    setMetricsLoading(true)
    setMetricsError('')
    const params =
      viewMode === 'factory' && factoryId && brandId
        ? { brandId, factoryId }
        : viewMode === 'factory' && factoryId && !brandId
          ? { factoryId }
          : { brandId }

    getDashboardMetrics(params)
      .then((data) => {
        if (!cancelled) setMetrics(data)
      })
      .catch((e) => {
        if (!cancelled) setMetricsError(e.message || 'Erro ao carregar métricas')
      })
      .finally(() => {
        if (!cancelled) setMetricsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [canLoadMetrics, viewMode, factoryId, brandId])

  function loadJobs() {
    if (!brandId) {
      setActiveJobs([])
      setArchivedJobs([])
      setJobsLoading(false)
      return
    }
    setJobsLoading(true)
    Promise.all([getJobs(false, brandId), getJobs(true, brandId)])
      .then(([active, archived]) => {
        setActiveJobs(active)
        setArchivedJobs(archived)
      })
      .catch((e) => setError(e.message))
      .finally(() => setJobsLoading(false))
  }

  useEffect(() => {
    loadJobs()
  }, [brandId])

  async function handleArchive(job) {
    try {
      await archiveJob(job.id)
      loadJobs()
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleDelete(job) {
    if (!confirm(`Deletar "${job.name || `Job #${job.id}`}"? O arquivo exportado será removido.`)) return
    try {
      await deleteJob(job.id)
      loadJobs()
    } catch (e) {
      setError(e.message)
    }
  }

  function toggleReschedulePlatform(id) {
    setReschedulePlatforms((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    )
  }

  async function handleReschedule(e) {
    e.preventDefault()
    if (!rescheduleJob || reschedulePlatforms.length === 0 || !rescheduleAt) return
    setRescheduling(true)
    try {
      await createScheduledPost(rescheduleJob.id, reschedulePlatforms, rescheduleAt)
      setRescheduleJob(null)
      setReschedulePlatforms([])
      setRescheduleAt('')
      loadJobs()
    } catch (e) {
      setError(e.message)
    } finally {
      setRescheduling(false)
    }
  }

  if (viewMode === 'factory' && !factoryId) {
    return (
      <div className="dashboard">
        <h1>Dashboard</h1>
        <p className="form-hint">Selecione uma factory no menu à esquerda para ver as métricas.</p>
      </div>
    )
  }

  if (viewMode === 'brand' && !brandId) {
    return (
      <div className="dashboard">
        <h1>Dashboard</h1>
        <p className="form-hint">Selecione uma marca no menu à esquerda para ver as métricas e seus jobs.</p>
      </div>
    )
  }

  const statusLabel = {
    QUEUED: 'Na fila',
    RUNNING: 'Processando',
    DONE: 'Concluído',
    FAILED: 'Falhou',
  }

  const jobs = tab === 'ativos' ? activeJobs : archivedJobs

  return (
    <div className="dashboard">
      <div className="page-header">
        <h1>Dashboard</h1>
        <Link to="/editar-videos" className="btn-primary">+ Editar vídeo</Link>
      </div>

      <section className="dashboard-metrics-section" aria-label="Métricas de processamento">
        <h2 className="dashboard-metrics-title">Processamento (AutoCut)</h2>
        {metricsLoading && (
          <p className="form-hint metrics-loading">Carregando métricas…</p>
        )}
        {metricsError && (
          <p className="page-error metrics-inline-error">{metricsError}</p>
        )}
        {!metricsLoading && !metricsError && metrics && (
          <>
            <div className="metrics-grid">
              <div className="metric-card">
                <span className="metric-label">Vídeos processados</span>
                <span className="metric-value">{formatInt(metrics.videos_processed)}</span>
                <span className="metric-hint">Análises AutoCut concluídas com sucesso</span>
              </div>
              <div className="metric-card">
                <span className="metric-label">Minutos processados</span>
                <span className="metric-value">{formatMinutesTotal(metrics.total_minutes_processed)}</span>
                <span className="metric-hint">Soma das durações de origem (chunks ou transcrição)</span>
              </div>
              <div className="metric-card">
                <span className="metric-label">Cortes finalizados</span>
                <span className="metric-value">{formatInt(metrics.finalized_cuts)}</span>
                <span className="metric-hint">Cortes com finalização concluída</span>
              </div>
              {metrics.active_brands != null && (
                <div className="metric-card">
                  <span className="metric-label">Brands no escopo</span>
                  <span className="metric-value">{formatInt(metrics.active_brands)}</span>
                </div>
              )}
              {metrics.active_factories != null && (
                <div className="metric-card">
                  <span className="metric-label">Factories ativas (escopo)</span>
                  <span className="metric-value">{formatInt(metrics.active_factories)}</span>
                </div>
              )}
              {metrics.avg_finalized_cuts_per_video != null && (
                <div className="metric-card">
                  <span className="metric-label">Média de cortes / vídeo</span>
                  <span className="metric-value">{formatDecimal(metrics.avg_finalized_cuts_per_video, 2)}</span>
                </div>
              )}
            </div>

            {metrics.breakdown_by_brand && metrics.breakdown_by_brand.length > 0 && (
              <div className="metrics-breakdown">
                <h3 className="metrics-breakdown-title">Resumo por brand</h3>
                <table className="metrics-table">
                  <thead>
                    <tr>
                      <th>Brand</th>
                      <th>Vídeos processados</th>
                      <th>Cortes finalizados</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.breakdown_by_brand.map((row) => (
                      <tr key={row.id}>
                        <td>{row.name}</td>
                        <td>{formatInt(row.videos_done)}</td>
                        <td>{formatInt(row.cuts_finalized)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      {!brandId && (
        <p className="form-hint dashboard-jobs-hint">
          Selecione uma brand no menu para ver jobs de edição (legado) e agendamentos.
        </p>
      )}

      {brandId && error && (
        <div className="page-error">{error}</div>
      )}

      {brandId && jobsLoading && (
        <div className="page-loading">Carregando jobs…</div>
      )}

      {brandId && !jobsLoading && !error && (
        <>
          <div className="tabs">
            <button
              className={tab === 'ativos' ? 'tab active' : 'tab'}
              onClick={() => setTab('ativos')}
            >
              Ativos ({activeJobs.length})
            </button>
            <button
              className={tab === 'arquivados' ? 'tab active' : 'tab'}
              onClick={() => setTab('arquivados')}
            >
              Arquivados ({archivedJobs.length})
            </button>
          </div>

          <div className="jobs-list">
            <h2>{tab === 'ativos' ? 'Seus jobs' : 'Jobs executados'}</h2>
            {jobs.length === 0 ? (
              <div className="empty-state">
                <p>{tab === 'ativos' ? 'Nenhum job ainda.' : 'Nenhum job arquivado.'}</p>
                {tab === 'ativos' && (
                  <Link to="/gerar-cortes" className="btn-primary">Gerar cortes</Link>
                )}
              </div>
            ) : (
              <div className="jobs-grid">
                {jobs.map((job) => (
                  <div key={job.id} className="job-card">
                    <div className="job-status" data-status={job.status}>
                      {statusLabel[job.status] || job.status}
                    </div>
                    <div className="job-info">
                      <span className="job-name">{job.name || `Job #${job.id}`}</span>
                      <span>{job.target_platforms?.join(', ') || '-'}</span>
                    </div>
                    {job.scheduled_summary && (
                      <div className="job-scheduled">
                        Agendado: {job.scheduled_summary.posted} postado(s)
                        {job.scheduled_summary.pending > 0 && `, ${job.scheduled_summary.pending} pendente(s)`}
                      </div>
                    )}
                    <div className="job-progress">
                      {job.status === 'RUNNING' && (
                        <div className="progress-bar">
                          <div className="progress-fill" style={{ width: `${job.progress || 0}%` }} />
                        </div>
                      )}
                    </div>
                    {job.status === 'DONE' && job.output_url && (
                      <button
                        type="button"
                        className="btn-download"
                        onClick={() => downloadJobVideo(job.id, job.name).catch((e) => setError(e.message))}
                      >
                        Baixar vídeo
                      </button>
                    )}
                    {job.status === 'FAILED' && (
                      <p className="job-error">{job.error}</p>
                    )}
                    <div className="job-actions">
                      {!job.archived && job.status === 'DONE' && job.output_url && (
                        <button
                          type="button"
                          className="btn-action"
                          onClick={() => {
                            setRescheduleJob(job)
                            setReschedulePlatforms(job.target_platforms || [])
                          }}
                        >
                          Reagendar
                        </button>
                      )}
                      {!job.archived && (
                        <button
                          type="button"
                          className="btn-action"
                          onClick={() => handleArchive(job)}
                        >
                          Arquivar
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-action btn-danger"
                        onClick={() => handleDelete(job)}
                      >
                        Deletar
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {rescheduleJob && (
        <div className="modal-overlay" onClick={() => setRescheduleJob(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Reagendar postagem</h3>
            <form onSubmit={handleReschedule}>
              <div className="form-group">
                <label>Redes</label>
                <div className="platforms">
                  {PLATFORMS.filter((p) => rescheduleJob.target_platforms?.includes(p.id)).map((p) => (
                    <label key={p.id} className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={reschedulePlatforms.includes(p.id)}
                        onChange={() => toggleReschedulePlatform(p.id)}
                      />
                      {p.label}
                    </label>
                  ))}
                </div>
              </div>
              <div className="form-group">
                <label>Data e hora</label>
                <input
                  type="datetime-local"
                  value={rescheduleAt}
                  onChange={(e) => setRescheduleAt(e.target.value)}
                  required
                />
              </div>
              <div className="modal-actions">
                <button type="button" onClick={() => setRescheduleJob(null)}>
                  Cancelar
                </button>
                <button type="submit" disabled={rescheduling}>
                  {rescheduling ? 'Agendando...' : 'Agendar'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
