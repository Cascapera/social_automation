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
  getFactoryYoutubeSummary,
} from '../api'
import PaginationControls, { DEFAULT_PAGE_SIZE } from '../components/PaginationControls'
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

/** Série diária de views (Upload Post): barras horizontais simples. */
/** Valor de cartão YouTube: evita exibir 0 quando não há dado de período (backend envia has_*). */
function ytSummaryNumber(value, hasMetricFlag, fmt) {
  if (hasMetricFlag === false) return '—'
  return fmt(value)
}

function brandYoutubeStatus(b) {
  if (b.error) return b.error
  const has = ['views', 'likes', 'comments', 'subscribers', 'videos_published'].some(
    (k) => b[k] != null,
  )
  return has ? 'OK' : 'Sem dados'
}

function YoutubeTimeseriesBars({ rows, formatInt: fmt }) {
  const list = Array.isArray(rows) ? rows.slice(-45) : []
  if (!list.length) return null
  const max = Math.max(...list.map((r) => r.views || 0), 1)
  return (
    <div className="yt-ts-wrap">
      {list.map((r) => (
        <div key={r.date} className="yt-ts-row">
          <span className="yt-ts-date">{r.date}</span>
          <div className="yt-ts-bar-track" aria-hidden>
            <div
              className="yt-ts-bar-fill"
              style={{ width: `${Math.max(4, ((r.views || 0) / max) * 100)}%` }}
            />
          </div>
          <span className="yt-ts-num">{fmt(r.views)}</span>
        </div>
      ))}
    </div>
  )
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
  const [jobsPageActive, setJobsPageActive] = useState(1)
  const [jobsPageArchived, setJobsPageArchived] = useState(1)
  const [jobsCountActive, setJobsCountActive] = useState(0)
  const [jobsCountArchived, setJobsCountArchived] = useState(0)
  const [ytData, setYtData] = useState(null)
  const [ytLoading, setYtLoading] = useState(false)
  const [ytError, setYtError] = useState('')
  const [ytPeriod, setYtPeriod] = useState('last_month')

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

  useEffect(() => {
    if (viewMode !== 'factory' || !factoryId) {
      setYtData(null)
      setYtLoading(false)
      setYtError('')
      return
    }
    let cancelled = false
    setYtLoading(true)
    setYtError('')
    getFactoryYoutubeSummary(factoryId, { period: ytPeriod })
      .then((data) => {
        if (!cancelled) setYtData(data)
      })
      .catch((e) => {
        if (!cancelled) setYtError(e.message || 'Erro ao carregar YouTube (Upload Post)')
      })
      .finally(() => {
        if (!cancelled) setYtLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [viewMode, factoryId, ytPeriod])

  function loadJobs() {
    if (!brandId) {
      setActiveJobs([])
      setArchivedJobs([])
      setJobsCountActive(0)
      setJobsCountArchived(0)
      setJobsLoading(false)
      return
    }
    setJobsLoading(true)
    Promise.all([
      getJobs(false, brandId, { page: jobsPageActive, pageSize: DEFAULT_PAGE_SIZE }),
      getJobs(true, brandId, { page: jobsPageArchived, pageSize: DEFAULT_PAGE_SIZE }),
    ])
      .then(([active, archived]) => {
        setActiveJobs(active.items)
        setArchivedJobs(archived.items)
        setJobsCountActive(active.count)
        setJobsCountArchived(archived.count)
      })
      .catch((e) => setError(e.message))
      .finally(() => setJobsLoading(false))
  }

  useEffect(() => {
    setJobsPageActive(1)
    setJobsPageArchived(1)
  }, [brandId])

  useEffect(() => {
    loadJobs()
  }, [brandId, jobsPageActive, jobsPageArchived])

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

      {viewMode === 'factory' && factoryId && (
        <section className="dashboard-youtube-section" aria-label="YouTube Upload Post">
          <div className="dashboard-youtube-header">
            <h2 className="dashboard-metrics-title">YouTube (Upload Post)</h2>
            <div className="dashboard-youtube-controls">
              <label className="yt-period-label">
                Período
                <select
                  value={ytPeriod}
                  onChange={(e) => setYtPeriod(e.target.value)}
                  disabled={ytLoading}
                >
                  <option value="last_week">Última semana</option>
                  <option value="last_month">Último mês</option>
                  <option value="last_3months">Últimos 3 meses</option>
                  <option value="last_year">Último ano</option>
                </select>
              </label>
            </div>
          </div>
          {ytLoading && <p className="form-hint metrics-loading">Carregando analytics YouTube…</p>}
          {ytError && <p className="page-error metrics-inline-error">{ytError}</p>}
          {!ytLoading && !ytError && ytData && (
            <>
              {ytData.meta?.config_error && (
                <div className="yt-config-banner" role="status">
                  {ytData.meta.config_error}
                </div>
              )}
              {ytData.meta?.date_range_note && !ytData.meta?.config_error && (
                <p className="form-hint yt-meta-note">{ytData.meta.date_range_note}</p>
              )}
              {ytData.meta?.has_period_metrics === false && !ytData.meta?.config_error && (
                <p className="form-hint yt-meta-note">
                  Nenhuma métrica de período foi retornada pelo Upload Post para as marcas desta factory.
                  Verifique o status por marca na tabela abaixo ou a conexão do perfil <code>brand_&lt;id&gt;</code>.
                </p>
              )}
              <div className="metrics-grid yt-summary-grid">
                <div className="metric-card">
                  <span className="metric-label">Visualizações (período)</span>
                  <span className="metric-value">
                    {ytSummaryNumber(
                      ytData.summary?.total_views,
                      ytData.meta?.has_period_metrics,
                      formatInt,
                    )}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Curtidas</span>
                  <span className="metric-value">
                    {ytSummaryNumber(ytData.summary?.total_likes, ytData.meta?.has_period_metrics, formatInt)}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Comentários</span>
                  <span className="metric-value">
                    {ytSummaryNumber(ytData.summary?.total_comments, ytData.meta?.has_period_metrics, formatInt)}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Compartilhamentos</span>
                  <span className="metric-value">
                    {ytSummaryNumber(ytData.summary?.total_shares, ytData.meta?.has_period_metrics, formatInt)}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Inscritos (snapshot)</span>
                  <span className="metric-value">
                    {ytSummaryNumber(
                      ytData.summary?.total_subscribers,
                      ytData.meta?.has_subscriber_data,
                      formatInt,
                    )}
                  </span>
                  <span className="metric-hint">Soma dos canais conectados (Upload Post)</span>
                </div>
                <div className="metric-card">
                  <span className="metric-label">Vídeos publicados (período)</span>
                  <span className="metric-value">
                    {ytSummaryNumber(
                      ytData.summary?.videos_published,
                      ytData.meta?.has_period_metrics,
                      formatInt,
                    )}
                  </span>
                </div>
                {ytData.summary?.avg_views_per_video != null && ytData.meta?.has_period_metrics !== false && (
                  <div className="metric-card">
                    <span className="metric-label">Média de views / vídeo</span>
                    <span className="metric-value">{formatDecimal(ytData.summary.avg_views_per_video, 1)}</span>
                  </div>
                )}
              </div>

              <div className="yt-subsection">
                <h3 className="yt-subsection-title">Views por dia</h3>
                {ytData.timeseries?.length ? (
                  <YoutubeTimeseriesBars rows={ytData.timeseries} formatInt={formatInt} />
                ) : (
                  <p className="form-hint">Sem série temporal para o período (ou perfis ainda sem dados no Upload Post).</p>
                )}
                {ytData.meta?.timeseries_note && (
                  <p className="form-hint yt-meta-note">{ytData.meta.timeseries_note}</p>
                )}
              </div>

              <div className="yt-subsection">
                <h3 className="yt-subsection-title">Top vídeos (views)</h3>
                {!ytData.top_posts?.length ? (
                  <p className="form-hint">
                    Nenhum post com métricas disponível. É necessário ter publicações via Upload Post com{' '}
                    <code>upload_post_request_id</code> registrado.
                  </p>
                ) : (
                  <table className="metrics-table yt-top-table">
                    <thead>
                      <tr>
                        <th>Título</th>
                        <th>Views</th>
                        <th>Curtidas</th>
                        <th>Comentários</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {ytData.top_posts.map((row) => (
                        <tr key={row.request_id}>
                          <td>{row.title || '—'}</td>
                          <td>{formatInt(row.views)}</td>
                          <td>{formatInt(row.likes)}</td>
                          <td>{formatInt(row.comments)}</td>
                          <td>
                            {row.post_url ? (
                              <a href={row.post_url} target="_blank" rel="noreferrer">
                                Abrir
                              </a>
                            ) : (
                              '—'
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {!!ytData.top_posts_engagement?.length && (
                <div className="yt-subsection">
                  <h3 className="yt-subsection-title">Top engajamento (likes + comentários + shares)</h3>
                  <table className="metrics-table yt-top-table">
                    <thead>
                      <tr>
                        <th>Título</th>
                        <th>Score</th>
                        <th>Views</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ytData.top_posts_engagement.map((row) => (
                        <tr key={`eng-${row.request_id}`}>
                          <td>{row.title || '—'}</td>
                          <td>{formatInt(row.engagement_score)}</td>
                          <td>{formatInt(row.views)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="yt-subsection">
                <h3 className="yt-subsection-title">Por marca / canal (Upload Post)</h3>
                {!ytData.brands?.length ? (
                  <p className="form-hint">Nenhuma brand nesta factory.</p>
                ) : (
                  <table className="metrics-table yt-brands-table">
                    <thead>
                      <tr>
                        <th>Marca</th>
                        <th>Perfil UP</th>
                        <th>Inscritos</th>
                        <th>Views (período)</th>
                        <th>Curtidas</th>
                        <th>Comentários</th>
                        <th>Vídeos</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ytData.brands.map((b) => (
                        <tr key={b.brand_id}>
                          <td>{b.brand_name}</td>
                          <td>
                            <code className="yt-code">{b.upload_post_profile}</code>
                          </td>
                          <td>{formatInt(b.subscribers)}</td>
                          <td>{formatInt(b.views)}</td>
                          <td>{formatInt(b.likes)}</td>
                          <td>{formatInt(b.comments)}</td>
                          <td>{formatInt(b.videos_published)}</td>
                          <td className={b.error ? 'yt-brand-err' : ''}>{brandYoutubeStatus(b)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </>
          )}
        </section>
      )}

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
              Ativos ({jobsCountActive})
            </button>
            <button
              className={tab === 'arquivados' ? 'tab active' : 'tab'}
              onClick={() => setTab('arquivados')}
            >
              Arquivados ({jobsCountArchived})
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
            <PaginationControls
              page={tab === 'ativos' ? jobsPageActive : jobsPageArchived}
              totalCount={tab === 'ativos' ? jobsCountActive : jobsCountArchived}
              onPageChange={(p) =>
                tab === 'ativos' ? setJobsPageActive(p) : setJobsPageArchived(p)
              }
              disabled={jobsLoading}
            />
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
