import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useBrand } from '../context/BrandContext'
import { getJobs, archiveJob, deleteJob, createScheduledPost, downloadJobVideo } from '../api'
import './Dashboard.css'

const PLATFORMS = [
  { id: 'IG', label: 'Instagram Reels' },
  { id: 'TT', label: 'TikTok' },
  { id: 'YT', label: 'YouTube Shorts' },
  { id: 'YTB', label: 'YouTube' },
]

export default function Dashboard() {
  const { brandId } = useBrand()
  const [activeJobs, setActiveJobs] = useState([])
  const [archivedJobs, setArchivedJobs] = useState([])
  const [tab, setTab] = useState('ativos')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [rescheduleJob, setRescheduleJob] = useState(null)
  const [reschedulePlatforms, setReschedulePlatforms] = useState([])
  const [rescheduleAt, setRescheduleAt] = useState('')
  const [rescheduling, setRescheduling] = useState(false)

  function loadJobs() {
    if (!brandId) {
      setActiveJobs([])
      setArchivedJobs([])
      setLoading(false)
      return
    }
    setLoading(true)
    Promise.all([getJobs(false, brandId), getJobs(true, brandId)])
      .then(([active, archived]) => {
        setActiveJobs(active)
        setArchivedJobs(archived)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
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

  if (!brandId) {
    return (
      <div className="dashboard">
        <h1>Dashboard</h1>
        <p className="form-hint">Selecione uma marca no menu à esquerda para ver seus jobs.</p>
      </div>
    )
  }
  if (loading) return <div className="page-loading">Carregando...</div>
  if (error) return <div className="page-error">{error}</div>

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
