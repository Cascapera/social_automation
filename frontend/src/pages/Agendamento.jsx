import { useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getJobs,
  getScheduledPosts,
  createScheduledPost,
  getBrandSocialAccounts,
  rescheduleScheduledPost,
  deleteScheduledPost,
} from '../api'
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

function CalendarioMensal({ scheduled, statusLabel }) {
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
            const hasPosted = posts.some((p) => p.status === 'DONE')
            const hasPending = posts.some((p) => p.status === 'PENDING' || p.status === 'POSTING')
            const hoje = cell.date && cell.date.toDateString() === now.toDateString()
            return (
              <div
                key={idx}
                className={`calendario-celula ${!cell.isCurrent ? 'outro-mes' : ''} ${hasPending ? 'tem-agendamentos' : ''} ${hasPosted ? 'teve-postado' : ''} ${hoje ? 'hoje' : ''}`}
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
  const [socialAccountId, setSocialAccountId] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')
  const [privacyStatus, setPrivacyStatus] = useState('private')
  const [socialAccounts, setSocialAccounts] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [showScheduleForm, setShowScheduleForm] = useState(false)
  const [expandedPostedDates, setExpandedPostedDates] = useState({})
  const [rescheduleModalOpen, setRescheduleModalOpen] = useState(false)
  const [rescheduleId, setRescheduleId] = useState(null)
  const [rescheduleAt, setRescheduleAt] = useState('')
  const [rescheduling, setRescheduling] = useState(false)

  useEffect(() => {
    if (brandId) {
      getJobs(false, brandId).then(setJobs).catch(() => setJobs([]))
      reloadScheduledPosts()
      getBrandSocialAccounts(brandId).then(setSocialAccounts).catch(() => setSocialAccounts([]))
    } else {
      setJobs([])
      setScheduled([])
      setSocialAccounts([])
    }
  }, [brandId])

  function reloadScheduledPosts() {
    if (!brandId) {
      setScheduled([])
      return
    }
    getScheduledPosts(brandId).then(setScheduled).catch(() => setScheduled([]))
  }

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
      await createScheduledPost({
        jobId: parseInt(jobId),
        platforms,
        scheduledAt,
        socialAccountId: socialAccountId ? parseInt(socialAccountId) : null,
        title: title.trim() || undefined,
        description: description.trim() || undefined,
        tags: tags ? tags.split(',').map((t) => t.trim()).filter(Boolean) : [],
        privacyStatus,
      })
      reloadScheduledPosts()
      setJobId('')
      setPlatforms([])
      setScheduledAt('')
      setTitle('')
      setDescription('')
      setTags('')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  function toDateTimeLocal(value) {
    const d = new Date(value)
    if (Number.isNaN(d.getTime())) return ''
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd}T${hh}:${mi}`
  }

  function openRescheduleModal(item) {
    setError('')
    setRescheduleId(item.id)
    setRescheduleAt(toDateTimeLocal(item.scheduled_at))
    setRescheduleModalOpen(true)
  }

  async function handleReschedule(e) {
    e.preventDefault()
    if (!rescheduleId || !rescheduleAt) {
      setError('Informe a nova data/hora para reagendar.')
      return
    }
    setError('')
    setRescheduling(true)
    try {
      await rescheduleScheduledPost(rescheduleId, rescheduleAt)
      setRescheduleModalOpen(false)
      setRescheduleId(null)
      setRescheduleAt('')
      reloadScheduledPosts()
    } catch (e) {
      setError(e.message)
    } finally {
      setRescheduling(false)
    }
  }

  async function handleCancelSchedule(item) {
    if (!confirm('Cancelar este agendamento?')) return
    setError('')
    try {
      await deleteScheduledPost(item.id)
      reloadScheduledPosts()
    } catch (e) {
      setError(e.message)
    }
  }

  const statusLabel = { PENDING: 'Pendente', POSTING: 'Postando', DONE: 'Postado', FAILED: 'Falhou' }
  const platformLabels = (arr) => (arr || []).map((p) => PLATFORMS.find((x) => x.id === p)?.label || p).join(', ')

  const sortedScheduled = [...scheduled].sort(
    (a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at)
  )
  const activeScheduled = sortedScheduled.filter((s) => s.status !== 'DONE')
  const postedScheduled = [...sortedScheduled]
    .filter((s) => s.status === 'DONE')
    .sort((a, b) => new Date(b.posted_at || b.scheduled_at) - new Date(a.posted_at || a.scheduled_at))

  const postedByDate = postedScheduled.reduce((acc, item) => {
    const d = new Date(item.posted_at || item.scheduled_at)
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    if (!acc[key]) acc[key] = []
    acc[key].push(item)
    return acc
  }, {})
  const postedDateKeys = Object.keys(postedByDate).sort((a, b) => (a < b ? 1 : -1))

  function formatGroupDate(key) {
    const [y, m, d] = key.split('-')
    return `${d}/${m}/${y}`
  }

  function togglePostedDate(key) {
    setExpandedPostedDates((prev) => ({ ...prev, [key]: !prev[key] }))
  }

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
        <div className="section-header">
          <h2>Calendário de agendamentos</h2>
          <span className="section-hint">Mês atual e próximo mês (inclui agendados e postados)</span>
        </div>
        <CalendarioMensal scheduled={sortedScheduled} statusLabel={statusLabel} />
      </section>

      <section className="section">
        <button
          type="button"
          className="collapse-toggle"
          onClick={() => setShowScheduleForm((v) => !v)}
        >
          {showScheduleForm ? 'Ocultar novo agendamento' : 'Novo agendamento'}
        </button>
        {showScheduleForm && (
          <form onSubmit={handleSchedule} className="schedule-form compact">
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
              <label>Data e hora</label>
              <input
                type="datetime-local"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                required
              />
            </div>
            <div className="form-group form-group-full">
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
            {(platforms.includes('YT') || platforms.includes('YTB')) && socialAccounts.length > 0 && (
              <div className="form-group">
                <label>Canal YouTube</label>
                <select value={socialAccountId} onChange={(e) => setSocialAccountId(e.target.value)}>
                  <option value="">Usar primeira conta da marca</option>
                  {socialAccounts.filter((a) => a.platform === 'YTB' || a.platform === 'YT').map((a) => (
                    <option key={a.id} value={a.id}>{a.account_name || a.channel_id || `Canal ${a.id}`}</option>
                  ))}
                </select>
              </div>
            )}
            <div className="form-group">
              <label>Visibilidade (YouTube)</label>
              <select value={privacyStatus} onChange={(e) => setPrivacyStatus(e.target.value)}>
                <option value="private">Privado</option>
                <option value="unlisted">Não listado</option>
                <option value="public">Público</option>
              </select>
            </div>
            <div className="form-group">
              <label>Título (YouTube)</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Opcional"
              />
            </div>
            <div className="form-group">
              <label>Tags</label>
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="tag1, tag2, tag3"
              />
            </div>
            <div className="form-group form-group-full">
              <label>Descrição (YouTube)</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                placeholder="Opcional"
              />
            </div>
            <div className="form-actions form-group-full">
              <button type="submit" disabled={saving}>
                {saving ? 'Agendando...' : 'Agendar'}
              </button>
            </div>
          </form>
        )}
      </section>

      <section className="section">
        <h2>Agendamentos ativos</h2>
        {activeScheduled.length === 0 ? (
          <p className="empty-msg">Nenhum agendamento ativo.</p>
        ) : (
          <div className="agenda-table">
            <div className="agenda-row header">
              <span>Data</span>
              <span>Hora</span>
              <span>Vídeo</span>
              <span>Plataformas</span>
              <span>Status</span>
              <span>Ações</span>
            </div>
            {activeScheduled.map((s) => (
              <div key={s.id} className="agenda-row" data-status={s.status}>
                <span>{new Date(s.scheduled_at).toLocaleDateString('pt-BR')}</span>
                <span>{new Date(s.scheduled_at).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</span>
                <span>{s.job_name}</span>
                <span>{platformLabels(s.platforms)}</span>
                <span className="status">{statusLabel[s.status] || s.status}</span>
                <span className="agenda-actions">
                  {s.status === 'FAILED' && (
                    <button
                      type="button"
                      className="btn-action"
                      onClick={() => openRescheduleModal(s)}
                    >
                      Reagendar
                    </button>
                  )}
                  {(s.status === 'PENDING' || s.status === 'FAILED') && (
                    <button
                      type="button"
                      className="btn-action btn-cancel"
                      onClick={() => handleCancelSchedule(s)}
                    >
                      Cancelar
                    </button>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="section">
        <h2>Vídeos postados</h2>
        {postedDateKeys.length === 0 ? (
          <p className="empty-msg">Nenhum vídeo postado ainda.</p>
        ) : (
          <div className="posted-groups">
            {postedDateKeys.map((key) => (
              <div key={key} className="posted-group">
                <button
                  type="button"
                  className="posted-group-header"
                  onClick={() => togglePostedDate(key)}
                >
                  <span>{expandedPostedDates[key] ? '▼' : '▶'} {formatGroupDate(key)}</span>
                  <span>{postedByDate[key].length} vídeo(s)</span>
                </button>
                {expandedPostedDates[key] && (
                  <div className="posted-table">
                    <div className="posted-row header">
                      <span>Hora</span>
                      <span>Vídeo</span>
                      <span>Plataformas</span>
                    </div>
                    {postedByDate[key].map((item) => (
                      <div key={item.id} className="posted-row">
                        <span>{new Date(item.posted_at || item.scheduled_at).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</span>
                        <span>{item.job_name}</span>
                        <span>{platformLabels(item.platforms)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {rescheduleModalOpen && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Reagendar postagem</h3>
            <form onSubmit={handleReschedule}>
              <div className="form-group">
                <label>Nova data e hora</label>
                <input
                  type="datetime-local"
                  value={rescheduleAt}
                  onChange={(e) => setRescheduleAt(e.target.value)}
                  required
                />
              </div>
              <div className="modal-actions">
                <button
                  type="button"
                  onClick={() => {
                    setRescheduleModalOpen(false)
                    setRescheduleId(null)
                    setRescheduleAt('')
                  }}
                >
                  Fechar
                </button>
                <button type="submit" disabled={rescheduling}>
                  {rescheduling ? 'Reagendando...' : 'Salvar e tentar novamente'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
