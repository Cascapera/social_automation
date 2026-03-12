import { Fragment, useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getJobs,
  getScheduledPosts,
  createScheduledPost,
  getBrandSocialAccounts,
  rescheduleScheduledPost,
  deleteScheduledPost,
  removeAwaitingScheduledPost,
  getBrand,
  getFactory,
  updateFactory,
  getFactorySchedules,
  triggerImmediateSchedule,
  triggerBrandImmediateSchedule,
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
const WEEKDAY_NAMES = ['Domingo', 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado']

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
  const { brandId, brands, viewMode, factoryId } = useBrand()
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
  const [removingAwaitingId, setRemovingAwaitingId] = useState(null)
  const [factoryInfo, setFactoryInfo] = useState(null)
  const [togglingFactorySchedule, setTogglingFactorySchedule] = useState(false)
  const [triggeringImmediate, setTriggeringImmediate] = useState(false)
  const [scheduleDateModalOpen, setScheduleDateModalOpen] = useState(false)
  const [scheduleTargetDate, setScheduleTargetDate] = useState('')
  const [scheduleForBrandId, setScheduleForBrandId] = useState(null)
  const [factoryWeekSchedules, setFactoryWeekSchedules] = useState([])
  const [dailyScheduleStartTime, setDailyScheduleStartTime] = useState('11:00')
  const [savingDailyScheduleTime, setSavingDailyScheduleTime] = useState(false)

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

    if (viewMode === 'factory' && factoryId) {
      loadFactoryInfo()
    } else if (brandId) {
      loadFactoryInfo()
    } else {
      setFactoryInfo(null)
    }
  }, [brandId, viewMode, factoryId])

  useEffect(() => {
    if (factoryInfo?.id) {
      loadFactoryWeekSchedules(factoryInfo.id)
    } else {
      setFactoryWeekSchedules([])
    }
  }, [factoryInfo?.id, viewMode, brandId])

  async function loadFactoryInfo() {
    if (viewMode === 'factory' && factoryId) {
      try {
        const f = await getFactory(factoryId)
        setFactoryInfo(f)
        setDailyScheduleStartTime((f?.daily_schedule_start_time || '19:00').toString().slice(0, 5))
      } catch {
        setFactoryInfo(null)
      }
      return
    }
    if (!brandId) {
      setFactoryInfo(null)
      return
    }
    try {
      const selected = (brands || []).find((b) => String(b.id) === String(brandId))
      let factoryId = selected?.factory
      if (!factoryId) {
        const brand = await getBrand(brandId)
        factoryId = brand?.factory
      }
      if (!factoryId) {
        setFactoryInfo(null)
        return
      }
      const f = await getFactory(factoryId)
      setFactoryInfo(f)
      setDailyScheduleStartTime((f?.daily_schedule_start_time || '19:00').toString().slice(0, 5))
    } catch {
      setFactoryInfo(null)
    }
  }

  async function loadFactoryWeekSchedules(factoryId) {
    try {
      const rows = await getFactorySchedules(
        factoryId,
        null,
        brandId || null,
      )
      setFactoryWeekSchedules(Array.isArray(rows) ? rows : [])
    } catch {
      setFactoryWeekSchedules([])
    }
  }

  function reloadScheduledPosts() {
    if (viewMode === 'factory' && factoryId) {
      getScheduledPosts({
        factoryId,
        brandId: brandId || null,
      }).then(setScheduled).catch(() => setScheduled([]))
      return
    }
    if (!brandId) {
      setScheduled([])
      return
    }
    getScheduledPosts({ brandId }).then(setScheduled).catch(() => setScheduled([]))
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

  async function handleRemoveAwaiting(item) {
    if (!item?.id) return
    if (!confirm('Excluir este vídeo do banco e remover o agendamento?')) return
    setError('')
    setRemovingAwaitingId(item.id)
    try {
      await removeAwaitingScheduledPost(item.id)
      reloadScheduledPosts()
      if (factoryInfo?.id) {
        await loadFactoryWeekSchedules(factoryInfo.id)
      }
    } catch (e) {
      setError(e.message || 'Falha ao remover vídeo aguardando postagem.')
    } finally {
      setRemovingAwaitingId(null)
    }
  }

  async function handleToggleFactorySchedule() {
    if (!factoryInfo?.id || togglingFactorySchedule) return
    setError('')
    setTogglingFactorySchedule(true)
    try {
      const updated = await updateFactory(factoryInfo.id, {
        scheduling_paused: !factoryInfo.scheduling_paused,
      })
      setFactoryInfo(updated)
    } catch (e) {
      setError(e.message)
    } finally {
      setTogglingFactorySchedule(false)
    }
  }

  function getTomorrowDateStr() {
    const d = new Date()
    d.setDate(d.getDate() + 1)
    return d.toISOString().slice(0, 10)
  }

  function getTodayDateStr() {
    return new Date().toISOString().slice(0, 10)
  }

  function handleOpenScheduleDateModal(forBrandId = null) {
    setScheduleForBrandId(forBrandId)
    setScheduleTargetDate(getTomorrowDateStr())
    setScheduleDateModalOpen(true)
    setError('')
  }

  async function handleConfirmScheduleDate() {
    if (triggeringImmediate || !scheduleTargetDate) return
    const hasFactory = !!factoryInfo?.id
    const brandIdToUse = scheduleForBrandId || brandId
    if (!hasFactory && !brandIdToUse) return
    if (hasFactory && !factoryInfo?.id) return
    setError('')
    setTriggeringImmediate(true)
    try {
      const result = hasFactory
        ? await triggerImmediateSchedule(
            factoryInfo.id,
            scheduleTargetDate,
            brandIdToUse || undefined,
          )
        : await triggerBrandImmediateSchedule(brandIdToUse, scheduleTargetDate)
      setScheduleDateModalOpen(false)
      setScheduleForBrandId(null)
      reloadScheduledPosts()
      if (factoryInfo?.id) {
        await loadFactoryWeekSchedules(factoryInfo.id)
      } else if (brandIdToUse && result?.factory_id) {
        await loadFactoryInfo()
        await loadFactoryWeekSchedules(result.factory_id)
      }
    } catch (e) {
      setError(e.message || 'Falha ao disparar agendamento.')
    } finally {
      setTriggeringImmediate(false)
    }
  }

  async function handleSaveDailyScheduleStartTime() {
    if (!factoryInfo?.id || savingDailyScheduleTime) return
    setSavingDailyScheduleTime(true)
    setError('')
    try {
      const value = dailyScheduleStartTime.trim() ? `${dailyScheduleStartTime.slice(0, 5)}:00` : null
      const updated = await updateFactory(factoryInfo.id, { daily_schedule_start_time: value || null })
      setFactoryInfo(updated)
    } catch (e) {
      setError(e.message || 'Erro ao salvar horário.')
    } finally {
      setSavingDailyScheduleTime(false)
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

  function getCurrentWeekRange() {
    const now = new Date()
    const start = new Date(now)
    const day = start.getDay()
    start.setDate(start.getDate() - day)
    start.setHours(0, 0, 0, 0)
    const end = new Date(start)
    end.setDate(end.getDate() + 7)
    return { start, end }
  }

  function buildFactoryWeeklyMatrix(items) {
    const { start, end } = getCurrentWeekRange()
    const filtered = (items || []).filter((item) => {
      const dt = new Date(item.scheduled_at)
      return dt >= start && dt < end
    })
    const hours = Array.from({ length: 24 }, (_, h) => h)
    const weekDays = Array.from({ length: 7 }, (_, i) => {
      const d = new Date(start)
      d.setDate(start.getDate() + i)
      return d
    })
    const brandNameById = (brands || []).reduce((acc, b) => {
      acc[String(b.id)] = b.name
      return acc
    }, {})
    const matrix = {}
    for (const h of hours) {
      matrix[h] = {}
      for (let d = 0; d < 7; d++) matrix[h][d] = []
    }

    const summaryByBrand = {}
    filtered.forEach((item) => {
      const dt = new Date(item.scheduled_at)
      const dayIndex = dt.getDay()
      const hour = dt.getHours()
      const brandName = brandNameById[String(item.brand)] || `Canal #${item.brand}`
      const status = item.status || 'PLANNED'
      const isPosted = status === 'DONE'
      const isPostedOnChannel = Boolean(item.posted_on_channel)
      const postedAt = item.posted_at ? new Date(item.posted_at) : null
      const postedTimeLabel = postedAt
        ? postedAt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
        : ''
      const tag = {
        id: item.id,
        brandId: item.brand,
        brandName,
        status,
        isPosted,
        isPostedOnChannel,
        postedTimeLabel,
        externalVideoId: item.external_video_id || '',
        timeLabel: dt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
      }
      matrix[hour][dayIndex].push(tag)
      if (!summaryByBrand[item.brand]) {
        summaryByBrand[item.brand] = {
          brandId: item.brand,
          brandName,
          posted: 0,
          scheduled: 0,
          total: 0,
        }
      }
      summaryByBrand[item.brand].total += 1
      if (isPosted) summaryByBrand[item.brand].posted += 1
      else summaryByBrand[item.brand].scheduled += 1
    })

    const channelSummary = Object.values(summaryByBrand).sort((a, b) => {
      if (a.total !== b.total) return a.total - b.total
      if (a.posted !== b.posted) return a.posted - b.posted
      return a.brandName.localeCompare(b.brandName)
    })

    return { start, weekDays, hours, matrix, channelSummary }
  }

  const factoryWeek = buildFactoryWeeklyMatrix(factoryWeekSchedules)

  return (
    <div className="agendamento">
      <h1>Agendamento</h1>
      <p className="page-desc">
        Selecione o vídeo e as plataformas para agendar a postagem.
      </p>

      {error && <div className="form-error">{error}</div>}

      {!brandId && viewMode !== 'factory' && (
        <p className="form-hint">Selecione uma marca no menu à esquerda para agendar postagens.</p>
      )}
      {!brandId && viewMode === 'factory' && (
        <p className="form-hint">Selecione uma brand da factory para criar/agendar jobs individuais. A visão semanal abaixo continua disponível sem selecionar brand.</p>
      )}

      {brandId && viewMode === 'brand' && (
        <section className="section brand-schedule-control">
          <div className="brand-control-info">
            <strong>Marca: {(brands || []).find((b) => String(b.id) === String(brandId))?.name || `Brand #${brandId}`}</strong>
            {!factoryInfo && (
              <p className="form-hint brand-no-factory-hint">
                Marca sem factory: agendamento sob demanda. Uma factory pessoal é criada automaticamente quando necessário.
              </p>
            )}
            {factoryInfo && (
              <div className="schedule-auto-toggle">
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={!factoryInfo.scheduling_paused}
                    onChange={handleToggleFactorySchedule}
                    disabled={togglingFactorySchedule}
                  />
                  <span className="slider" />
                </label>
                <span className="switch-label">
                  {factoryInfo.scheduling_paused ? 'Off' : 'On'} — Agendamento automático às 19h
                </span>
              </div>
            )}
          </div>
          <div className="factory-control-buttons">
            <button
              type="button"
              className="factory-toggle-btn immediate"
              onClick={() => handleOpenScheduleDateModal(brandId)}
              disabled={triggeringImmediate}
              title="Agenda vídeos disponíveis desta marca para o dia selecionado."
            >
              {triggeringImmediate ? 'Agendando...' : 'Agendamento Imediato'}
            </button>
          </div>
        </section>
      )}

      {factoryInfo && viewMode === 'factory' && (
        <section className="section factory-schedule-control">
          <div className="factory-control-info">
            <strong>Factory: {factoryInfo.name}</strong>
            <div className="schedule-auto-toggle">
              <label className="switch">
                <input
                  type="checkbox"
                  checked={!factoryInfo.scheduling_paused}
                  onChange={handleToggleFactorySchedule}
                  disabled={togglingFactorySchedule}
                />
                <span className="slider" />
              </label>
              <span className="switch-label">
                {factoryInfo.scheduling_paused ? 'Off' : 'On'} — Agendamento automático às 19h
              </span>
            </div>
            {factoryInfo.scheduling_paused && (
              <div className="factory-paused-warning">
                Factory pausada: geração de cortes continua normal, mas o agendamento/publicação ficam em espera.
              </div>
            )}
          </div>
          <div className="factory-daily-schedule-time">
            <label htmlFor="daily-schedule-start">Horário fixo para agendar o dia seguinte:</label>
            <input
              id="daily-schedule-start"
              type="time"
              value={dailyScheduleStartTime}
              onChange={(e) => setDailyScheduleStartTime(e.target.value)}
            />
            <button
              type="button"
              className="factory-toggle-btn"
              onClick={handleSaveDailyScheduleStartTime}
              disabled={savingDailyScheduleTime}
            >
              {savingDailyScheduleTime ? 'Salvando...' : 'Salvar horário'}
            </button>
          </div>
          <p className="form-hint factory-daily-hint">
            Nesse horário o sistema agenda os vídeos disponíveis para o dia seguinte conforme os horários fixos de cada brand.
          </p>
          <div className="factory-control-buttons">
            <button
              type="button"
              className="factory-toggle-btn immediate"
              onClick={handleOpenScheduleDateModal}
              disabled={triggeringImmediate}
              title="Agenda vídeos disponíveis para o dia selecionado. Útil para agendar o fim de semana na sexta."
            >
              {triggeringImmediate ? 'Agendando...' : 'Agendamento Imediato'}
            </button>
          </div>
        </section>
      )}

      {factoryInfo && (
        <section className="section weekly-factory-section">
          <div className="section-header">
            <h2>Agenda semanal da Factory</h2>
            <span className="section-hint">
              Semana atual: {factoryWeek.start.toLocaleDateString('pt-BR')} -{' '}
              {factoryWeek.weekDays[6]?.toLocaleDateString('pt-BR')}
            </span>
          </div>

          <div className="weekly-summary">
            <h3>Resumo por canal (menor volume primeiro)</h3>
            {factoryWeek.channelSummary.length === 0 ? (
              <p className="empty-msg">Nenhum agendamento da semana atual para esta factory.</p>
            ) : (
              <div className="weekly-summary-list">
                {factoryWeek.channelSummary.map((s) => (
                  <div key={s.brandId} className="weekly-summary-item">
                    <strong>{s.brandName}</strong>
                    <span>Total: {s.total}</span>
                    <span>Agendados: {s.scheduled}</span>
                    <span>Postados: {s.posted}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="weekly-grid-wrapper">
            <div className="weekly-grid">
              <div className="weekly-head hour">Hora</div>
              {factoryWeek.weekDays.map((d, idx) => (
                <div key={idx} className="weekly-head day">
                  <div>{WEEKDAY_NAMES[d.getDay()]}</div>
                  <small>{d.toLocaleDateString('pt-BR')}</small>
                </div>
              ))}

              {factoryWeek.hours.map((hour) => (
                <Fragment key={hour}>
                  <div className="weekly-hour">{String(hour).padStart(2, '0')}:00</div>
                  {factoryWeek.weekDays.map((d, idx) => {
                    const tags = factoryWeek.matrix[hour]?.[idx] || []
                    return (
                      <div key={`${hour}-${idx}`} className="weekly-cell">
                        {tags.map((tag) => (
                          <span
                            key={tag.id}
                            className={`weekly-tag ${
                              tag.isPostedOnChannel
                                ? 'posted-channel'
                                : tag.isPosted
                                  ? 'posted'
                                  : 'scheduled'
                            }`}
                            title={`${tag.brandName} - ${tag.timeLabel} - ${
                              tag.isPostedOnChannel
                                ? `Postado no canal${tag.postedTimeLabel ? ` às ${tag.postedTimeLabel}` : ''}`
                                : tag.isPosted
                                  ? 'Postado (interno)'
                                  : 'Agendado'
                            }${tag.externalVideoId ? ` - ID: ${tag.externalVideoId}` : ''}`}
                          >
                            {tag.brandName} {tag.timeLabel}{' '}
                            {tag.isPostedOnChannel
                              ? `Postado no canal${tag.postedTimeLabel ? ` ${tag.postedTimeLabel}` : ''}`
                              : tag.isPosted
                                ? 'Postado'
                                : 'Agendado'}
                          </span>
                        ))}
                      </div>
                    )
                  })}
                </Fragment>
              ))}
            </div>
          </div>
        </section>
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
                  {(s.status === 'PENDING' || s.status === 'FAILED') && (
                    <button
                      type="button"
                      className="btn-action btn-cancel"
                      onClick={() => handleRemoveAwaiting(s)}
                      disabled={removingAwaitingId === s.id}
                      title="Remove do agendamento e do banco de vídeos aguardando"
                    >
                      {removingAwaitingId === s.id ? 'Removendo...' : 'Excluir do banco'}
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

      {scheduleDateModalOpen && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Agendar para qual dia?</h3>
            <p className="form-hint">
              O sistema agenda os vídeos disponíveis no banco para a data selecionada, respeitando os horários fixos de cada brand.
            </p>
            <div className="form-group">
              <label htmlFor="schedule-target-date">Data</label>
              <input
                id="schedule-target-date"
                type="date"
                value={scheduleTargetDate}
                onChange={(e) => setScheduleTargetDate(e.target.value)}
                min={getTodayDateStr()}
              />
            </div>
            <div className="modal-actions">
              <button
                type="button"
                onClick={() => {
                  setScheduleDateModalOpen(false)
                  setScheduleForBrandId(null)
                }}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="primary"
                onClick={handleConfirmScheduleDate}
                disabled={triggeringImmediate || !scheduleTargetDate}
              >
                {triggeringImmediate ? 'Agendando...' : 'Agendar'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
