import { useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import { getJobs, getScheduledPosts, createScheduledPost } from '../api'
import { PlatformIcon } from '../components/PlatformIcons'
import './Agendamento.css'

const PLATFORMS = [
  { id: 'IG', label: 'Instagram Reels' },
  { id: 'TT', label: 'TikTok' },
  { id: 'YT', label: 'YouTube Shorts' },
  { id: 'YTB', label: 'YouTube' },
]

const WEEKDAYS = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
const MONTHS = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']

function CalendarioMensal({ scheduled, platformLabels, statusLabel }) {
  const now = new Date()
  const mesAtual = { year: now.getFullYear(), month: now.getMonth() }
  const mesSeguinte = now.getMonth() === 11
    ? { year: now.getFullYear() + 1, month: 0 }
    : { year: now.getFullYear(), month: now.getMonth() + 1 }

  function buildMonthGrid(year, month) {
    const first = new Date(year, month, 1)
    const last = new Date(year, month + 1, 0)
    const firstDay = first.getDay()
    const lastDate = last.getDate()
    const cells = []
    for (let i = 0; i < firstDay; i++) cells.push({ day: null, isCurrent: false })
    for (let d = 1; d <= lastDate; d++) cells.push({ day: d, isCurrent: true, date: new Date(year, month, d) })
    const remaining = 42 - cells.length
    for (let i = 0; i < remaining; i++) cells.push({ day: null, isCurrent: false })
    return cells
  }

  function getPostsForDate(date) {
    if (!date) return []
    const y = date.getFullYear()
    const m = date.getMonth()
    const d = date.getDate()
    return scheduled.filter((s) => {
      const sd = new Date(s.scheduled_at)
      return sd.getFullYear() === y && sd.getMonth() === m && sd.getDate() === d
    })
  }

  /** Agrupa posts do dia por plataforma: { IG: [{job_name, time, status}], ... } */
  function groupPostsByPlatform(posts) {
    const byPlatform = {}
    posts.forEach((s) => {
      const time = new Date(s.scheduled_at).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
      const line = `${time} - ${s.job_name}${s.status ? ` (${statusLabel[s.status]})` : ''}`
      ;(s.platforms || []).forEach((p) => {
        if (!byPlatform[p]) byPlatform[p] = []
        byPlatform[p].push(line)
      })
    })
    return byPlatform
  }

  function MesCalendario({ year, month }) {
    const cells = buildMonthGrid(year, month)
    const monthName = `${MONTHS[month]} ${year}`

    return (
      <div className="mes-calendario">
        <h3 className="mes-titulo">{monthName}</h3>
        <div className="calendario-grid">
          {WEEKDAYS.map((w) => (
            <div key={w} className="calendario-dia-header">{w}</div>
          ))}
          {cells.map((cell, idx) => {
            const posts = cell.date ? getPostsForDate(cell.date) : []
            const byPlatform = groupPostsByPlatform(posts)
            const platformIds = Object.keys(byPlatform)
            const hoje = cell.date && cell.date.toDateString() === now.toDateString()
            return (
              <div
                key={idx}
                className={`calendario-celula ${!cell.isCurrent ? 'outro-mes' : ''} ${platformIds.length > 0 ? 'tem-agendamentos' : ''} ${hoje ? 'hoje' : ''}`}
              >
                {cell.day !== null && <span className="dia-numero">{cell.day}</span>}
                {platformIds.length > 0 && (
                  <div className="celula-icones">
                    {platformIds.map((pid) => {
                      const label = PLATFORMS.find((x) => x.id === pid)?.label || pid
                      const tooltip = `${label}: ${byPlatform[pid].join(' • ')}`
                      return (
                        <span
                          key={pid}
                          className="celula-icone-wrapper"
                          title={tooltip}
                        >
                          <PlatformIcon platformId={pid} />
                        </span>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="calendario-mensal">
      <div className="calendario-meses">
        <MesCalendario year={mesAtual.year} month={mesAtual.month} />
        <MesCalendario year={mesSeguinte.year} month={mesSeguinte.month} />
      </div>
    </div>
  )
}

export default function Agendamento() {
  const { brandId } = useBrand()
  const [jobs, setJobs] = useState([])
  const [scheduled, setScheduled] = useState([])
  const [jobId, setJobId] = useState('')
  const [platforms, setPlatforms] = useState([])
  const [scheduledAt, setScheduledAt] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState('agenda')

  useEffect(() => {
    if (brandId) {
      getJobs(false, brandId).then(setJobs).catch(() => setJobs([]))
      getScheduledPosts(brandId).then(setScheduled).catch(() => setScheduled([]))
    } else {
      setJobs([])
      setScheduled([])
    }
  }, [brandId])

  const finishedJobs = jobs.filter((j) => j.status === 'DONE' && j.output_url)

  function togglePlatform(id) {
    setPlatforms((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    )
  }

  async function handleSchedule(e) {
    e.preventDefault()
    if (!jobId || platforms.length === 0 || !scheduledAt) {
      setError('Selecione vídeo, plataformas e data/hora')
      return
    }
    setError('')
    setSaving(true)
    try {
      await createScheduledPost(parseInt(jobId), platforms, scheduledAt)
      getScheduledPosts().then(setScheduled)
      setJobId('')
      setPlatforms([])
      setScheduledAt('')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const statusLabel = { PENDING: 'Pendente', POSTING: 'Postando', DONE: 'Postado', FAILED: 'Falhou' }
  const platformLabels = (arr) => (arr || []).map((p) => PLATFORMS.find((x) => x.id === p)?.label || p).join(', ')

  const sortedScheduled = [...scheduled].sort(
    (a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at)
  )

  return (
    <div className="agendamento">
      <h1>Agendamento</h1>
      <p className="page-desc">
        Selecione o vídeo e as plataformas para agendar a postagem.
      </p>

      {error && <div className="form-error">{error}</div>}

      {!brandId && (
        <p className="form-hint">Selecione uma marca no menu à esquerda para agendar postagens.</p>
      )}

      <section className="section">
        <h2>Novo agendamento</h2>
        <form onSubmit={handleSchedule} className="schedule-form">
          <div className="form-group">
            <label>Vídeo</label>
            <select value={jobId} onChange={(e) => setJobId(e.target.value)} required>
              <option value="">Selecione</option>
              {finishedJobs.map((j) => (
                <option key={j.id} value={j.id}>{j.name || `Job #${j.id}`}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Plataformas</label>
            <div className="platforms">
              {PLATFORMS.map((p) => (
                <label key={p.id} className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={platforms.includes(p.id)}
                    onChange={() => togglePlatform(p.id)}
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
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
              required
            />
          </div>
          <button type="submit" disabled={saving}>
            {saving ? 'Agendando...' : 'Agendar'}
          </button>
        </form>
      </section>

      <section className="section">
        <div className="agenda-header">
          <h2>Agenda</h2>
          <div className="view-tabs">
            <button
              className={view === 'agenda' ? 'active' : ''}
              onClick={() => setView('agenda')}
            >
              Lista
            </button>
            <button
              className={view === 'calendario' ? 'active' : ''}
              onClick={() => setView('calendario')}
            >
              Calendário
            </button>
          </div>
        </div>

        {view === 'agenda' ? (
          sortedScheduled.length === 0 ? (
            <p className="empty-msg">Nenhum agendamento.</p>
          ) : (
            <div className="agenda-table">
              <div className="agenda-row header">
              <span>Data</span>
              <span>Hora</span>
              <span>Vídeo</span>
              <span>Plataformas</span>
              <span>Status</span>
              </div>
              {sortedScheduled.map((s) => (
              <div key={s.id} className="agenda-row" data-status={s.status}>
                <span>{new Date(s.scheduled_at).toLocaleDateString('pt-BR')}</span>
                <span>{new Date(s.scheduled_at).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</span>
                <span>{s.job_name}</span>
                <span>{platformLabels(s.platforms)}</span>
                <span>{statusLabel[s.status] || s.status}</span>
                </div>
              ))}
            </div>
          )
        ) : (
          <CalendarioMensal
            scheduled={sortedScheduled}
            platformLabels={platformLabels}
            statusLabel={statusLabel}
          />
        )}
      </section>
    </div>
  )
}
