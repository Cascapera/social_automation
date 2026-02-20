import { useState, useEffect } from 'react'
import {
  getBrandAssets,
  getCuts,
  getJobs,
  createJob,
  runJob,
  getJob,
  deleteJob,
  uploadJob,
  downloadJobVideo,
  generateSubtitles,
  updateSubtitles,
  burnSubtitles,
} from '../api'
import { useBrand } from '../context/BrandContext'
import './EditarVideos.css'

const PLATFORMS = [
  { id: 'IG', label: 'Instagram Reels' },
  { id: 'TT', label: 'TikTok' },
  { id: 'YT', label: 'YouTube Shorts' },
  { id: 'YTB', label: 'YouTube' },
]

export default function EditarVideos() {
  const { brandId } = useBrand()
  const [intros, setIntros] = useState([])
  const [outros, setOutros] = useState([])
  const [cuts, setCuts] = useState([])
  const [jobs, setJobs] = useState([])
  const [jobName, setJobName] = useState('')
  const [selectedCuts, setSelectedCuts] = useState([])
  const [introAsset, setIntroAsset] = useState(null)
  const [outroAsset, setOutroAsset] = useState(null)
  const [makeVertical, setMakeVertical] = useState(true)
  const [transition, setTransition] = useState('none')
  const [transitionDuration, setTransitionDuration] = useState(0.5)
  const [creating, setCreating] = useState(false)
  const [runningJobId, setRunningJobId] = useState(null)
  const [pollingJob, setPollingJob] = useState(null)
  const [error, setError] = useState('')
  const [uploadJobFile, setUploadJobFile] = useState(null)
  const [uploadJobName, setUploadJobName] = useState('')
  const [uploadJobFormat, setUploadJobFormat] = useState('vertical')
  const [uploadingJob, setUploadingJob] = useState(false)
  const [cutsModalOpen, setCutsModalOpen] = useState(false)
  const [cutsSearch, setCutsSearch] = useState('')
  const [cutsFilterDate, setCutsFilterDate] = useState('all')
  const [cutsFilterDuration, setCutsFilterDuration] = useState('all')
  const [subtitleModalJob, setSubtitleModalJob] = useState(null)
  const [subtitlePollingJob, setSubtitlePollingJob] = useState(null)

  useEffect(() => {
    if (brandId) {
      getCuts(null, brandId).then(setCuts).catch(() => setCuts([]))
    } else {
      setCuts([])
    }
  }, [brandId])

  useEffect(() => {
    if (brandId) {
      getJobs(false, brandId).then(setJobs).catch(() => setJobs([]))
    } else {
      setJobs([])
    }
  }, [brandId])

  useEffect(() => {
    if (brandId) {
      getBrandAssets(brandId, 'INTRO').then(setIntros).catch(() => setIntros([]))
      Promise.all([
        getBrandAssets(brandId, 'OUTRO').catch(() => []),
        getBrandAssets(brandId, 'CTA').catch(() => []),
      ]).then(([o, c]) => setOutros([...o, ...c]))
    } else {
      setIntros([])
      setOutros([])
    }
  }, [brandId])

  useEffect(() => {
    if (!pollingJob) return
    const id = setInterval(async () => {
      try {
        const j = await getJob(pollingJob.id)
        setJobs((prev) => prev.map((x) => (x.id === j.id ? j : x)))
        if (j.status === 'DONE' || j.status === 'FAILED') {
          setPollingJob(null)
          setRunningJobId(null)
        }
      } catch {
        setPollingJob(null)
        setRunningJobId(null)
      }
    }, 2000)
    return () => clearInterval(id)
  }, [pollingJob])

  useEffect(() => {
    if (!subtitlePollingJob) return
    const id = setInterval(async () => {
      try {
        const j = await getJob(subtitlePollingJob.id)
        setJobs((prev) => prev.map((x) => (x.id === j.id ? j : x)))
        if (subtitleModalJob?.id === j.id) {
          setSubtitleModalJob(j)
        }
        if (!['generating', 'burning'].includes(j.subtitle_status || '')) {
          setSubtitlePollingJob(null)
        }
      } catch {
        setSubtitlePollingJob(null)
      }
    }, 2000)
    return () => clearInterval(id)
  }, [subtitlePollingJob, subtitleModalJob?.id])

  function addCutToJob(cutId) {
    if (!selectedCuts.includes(cutId)) {
      setSelectedCuts((prev) => [...prev, cutId])
    }
  }

  function removeCutFromJob(cutId) {
    setSelectedCuts((prev) => prev.filter((x) => x !== cutId))
  }

  function moveCutUp(index) {
    if (index <= 0) return
    setSelectedCuts((prev) => {
      const next = [...prev]
      ;[next[index - 1], next[index]] = [next[index], next[index - 1]]
      return next
    })
  }

  function moveCutDown(index) {
    if (index >= selectedCuts.length - 1) return
    setSelectedCuts((prev) => {
      const next = [...prev]
      ;[next[index], next[index + 1]] = [next[index + 1], next[index]]
      return next
    })
  }

  const formatLabel = (f) => (f === 'vertical' ? 'Vertical' : 'Horizontal')

  function formatDuration(sec) {
    if (sec == null || sec === undefined) return '-'
    const m = Math.floor(sec / 60)
    const s = Math.floor(sec % 60)
    return m > 0 ? `${m}min ${s}s` : `${s}s`
  }

  const filteredCuts = cuts.filter((c) => {
    if (cutsSearch.trim()) {
      const matchName = (c.name || '').toLowerCase().includes(cutsSearch.toLowerCase())
      const matchId = String(c.id).includes(cutsSearch)
      if (!matchName && !matchId) return false
    }
    if (cutsFilterDate !== 'all') {
      const created = new Date(c.created_at)
      const now = new Date()
      const days = (now - created) / (1000 * 60 * 60 * 24)
      if (cutsFilterDate === '7d' && days > 7) return false
      if (cutsFilterDate === '30d' && days > 30) return false
    }
    if (cutsFilterDuration !== 'all' && c.duration != null) {
      const d = c.duration
      if (cutsFilterDuration === '30' && d > 30) return false
      if (cutsFilterDuration === '30-60' && (d < 30 || d > 60)) return false
      if (cutsFilterDuration === '60-180' && (d < 60 || d > 180)) return false
      if (cutsFilterDuration === '180' && d < 180) return false
    }
    return true
  })
  const availableCuts = filteredCuts.filter((c) => !selectedCuts.includes(c.id))

  async function handleUploadJob(e) {
    e.preventDefault()
    if (!brandId) {
      setError('Selecione uma marca no menu à esquerda')
      return
    }
    if (!uploadJobFile) {
      setError('Selecione um arquivo de vídeo')
      return
    }
    setError('')
    setUploadingJob(true)
    try {
      const j = await uploadJob(uploadJobFile, uploadJobName, uploadJobFormat, brandId)
      setJobs((prev) => [j, ...prev])
      setUploadJobFile(null)
      setUploadJobName('')
      setUploadJobFormat('vertical')
    } catch (e) {
      setError(e.message)
    } finally {
      setUploadingJob(false)
    }
  }

  async function handleCreate(e) {
    e.preventDefault()
    if (!brandId) {
      setError('Selecione uma marca no menu à esquerda')
      return
    }
    if (selectedCuts.length === 0) {
      setError('Selecione pelo menos um corte')
      return
    }
    setError('')
    setCreating(true)
    try {
      const j = await createJob({
        name: jobName.trim() || undefined,
        cut_ids: selectedCuts,
        target_platforms: ['YT'],
        make_vertical: makeVertical,
        intro_asset: introAsset || null,
        outro_asset: outroAsset || null,
        transition,
        transition_duration: transitionDuration,
      }, brandId)
      await runJob(j.id)
      setJobs((prev) => [j, ...prev])
      setRunningJobId(j.id)
      setPollingJob(j)
      setSelectedCuts([])
      setJobName('')
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  async function handleRunJob(job) {
    if (job.status === 'RUNNING') return
    setRunningJobId(job.id)
    try {
      await runJob(job.id)
      setJobs((prev) => prev.map((j) => (j.id === job.id ? { ...j, status: 'QUEUED' } : j)))
      setPollingJob(job)
    } catch (e) {
      setError(e.message)
    } finally {
      setRunningJobId(null)
    }
  }

  async function handleGenerateSubtitles(job) {
    try {
      await generateSubtitles(job.id)
      setSubtitlePollingJob(job)
      setJobs((prev) => prev.map((j) => (j.id === job.id ? { ...j, subtitle_status: 'generating' } : j)))
    } catch (e) {
      setError(e.message)
    }
  }

  function handleOpenSubtitleModal(job) {
    setSubtitleModalJob({ ...job })
  }

  async function handleSaveSubtitles(segments, style) {
    if (!subtitleModalJob) return
    try {
      const j = await updateSubtitles(subtitleModalJob.id, segments, style)
      setSubtitleModalJob(j)
      setJobs((prev) => prev.map((x) => (x.id === j.id ? j : x)))
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleBurnSubtitles() {
    if (!subtitleModalJob) return
    try {
      await burnSubtitles(subtitleModalJob.id)
      setSubtitlePollingJob(subtitleModalJob)
      setSubtitleModalJob(null)
      setJobs((prev) => prev.map((j) => (j.id === subtitleModalJob.id ? { ...j, subtitle_status: 'burning' } : j)))
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleDelete(job) {
    if (!job.can_delete) {
      setError('Não é possível deletar: há agendamento pendente.')
      return
    }
    if (!confirm(`Deletar "${job.name || `Job #${job.id}`}"?`)) return
    try {
      await deleteJob(job.id)
      setJobs((prev) => prev.filter((x) => x.id !== job.id))
    } catch (e) {
      setError(e.message)
    }
  }

  const statusLabel = { QUEUED: 'Na fila', RUNNING: 'Processando', DONE: 'Concluído', FAILED: 'Falhou' }
  const finishedJobs = jobs.filter((j) => j.status === 'DONE' || j.status === 'FAILED') || []
  const queueJobs = jobs.filter((j) => j.status === 'QUEUED' || j.status === 'RUNNING')

  return (
    <div className="editar-videos">
      <h1>Editar Vídeos</h1>
      <p className="page-desc">Junte intro, cortes e outro. Escolha transição e formato. Adicione na fila.</p>

      {error && <div className="form-error">{error}</div>}

      <section className="section">
        <h2>Ou: Upload de vídeo pronto</h2>
        <p className="section-hint">Já tem o vídeo final editado? Envie o arquivo. O formato será detectado automaticamente.</p>
        <form onSubmit={handleUploadJob} className="upload-job-form">
          <input
            type="text"
            placeholder="Nome (opcional)"
            value={uploadJobName}
            onChange={(e) => setUploadJobName(e.target.value)}
          />
          <select value={uploadJobFormat} onChange={(e) => setUploadJobFormat(e.target.value)}>
            <option value="vertical">Vertical</option>
            <option value="horizontal">Horizontal</option>
          </select>
          <input
            type="file"
            accept="video/*"
            onChange={(e) => setUploadJobFile(e.target.files?.[0] || null)}
            required
          />
          <button type="submit" disabled={uploadingJob}>
            {uploadingJob ? 'Analisando...' : 'Enviar vídeo'}
          </button>
        </form>
      </section>

      <section className="section form-section">
        <h2>Novo vídeo</h2>
        {!brandId && (
          <p className="form-hint">Selecione uma marca no menu à esquerda para continuar.</p>
        )}
        <form onSubmit={handleCreate} className="job-form">
          <div className="form-group">
            <label>Nome</label>
            <input
              type="text"
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
              placeholder="Ex: Live 18/02"
            />
          </div>
          <div className="form-group">
            <label>Intro</label>
            <select value={introAsset || ''} onChange={(e) => setIntroAsset(e.target.value || null)}>
              <option value="">Nenhuma</option>
              {intros.map((a) => (
                <option key={a.id} value={a.id}>{a.label || a.asset_type}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Cortes (ordem)</label>
            <div className="cuts-selector">
              <button
                type="button"
                className="btn-add-cuts"
                onClick={() => setCutsModalOpen(true)}
              >
                + Adicionar cortes
              </button>
              {selectedCuts.length > 0 && (
                <span className="cuts-count">
                  {selectedCuts.length} corte{selectedCuts.length !== 1 ? 's' : ''} selecionado{selectedCuts.length !== 1 ? 's' : ''}
                </span>
              )}
            </div>
          </div>

          {cutsModalOpen && (
            <div className="cuts-modal-overlay" onClick={() => setCutsModalOpen(false)}>
              <div className="cuts-modal" onClick={(e) => e.stopPropagation()}>
                <div className="cuts-modal-header">
                  <h3>Selecionar cortes (ordem de inclusão)</h3>
                  <button type="button" className="cuts-modal-close" onClick={() => setCutsModalOpen(false)}>✕</button>
                </div>
                <div className="cuts-modal-body">
                  <div className="cuts-modal-panels">
                    <div className="cuts-panel">
                      <h4>Disponíveis</h4>
                      <div className="cuts-filters">
                        <input
                          type="text"
                          placeholder="Nome ou ID..."
                          value={cutsSearch}
                          onChange={(e) => setCutsSearch(e.target.value)}
                          className="cuts-search"
                        />
                        <select
                          value={cutsFilterDate}
                          onChange={(e) => setCutsFilterDate(e.target.value)}
                          className="cuts-filter-select"
                        >
                          <option value="all">Todas as datas</option>
                          <option value="7d">Últimos 7 dias</option>
                          <option value="30d">Últimos 30 dias</option>
                        </select>
                        <select
                          value={cutsFilterDuration}
                          onChange={(e) => setCutsFilterDuration(e.target.value)}
                          className="cuts-filter-select"
                        >
                          <option value="all">Qualquer duração</option>
                          <option value="30">Até 30s</option>
                          <option value="30-60">30s - 1min</option>
                          <option value="60-180">1min - 3min</option>
                          <option value="180">Acima de 3min</option>
                        </select>
                      </div>
                      <div className="cuts-list">
                        {availableCuts.length === 0 ? (
                          <p className="cuts-empty">{cutsSearch ? 'Nenhum corte encontrado' : 'Todos os cortes já foram adicionados'}</p>
                        ) : (
                          availableCuts.map((c) => (
                            <div
                              key={c.id}
                              className="cuts-list-item"
                              onClick={() => addCutToJob(c.id)}
                            >
                              <span className="cut-name">{c.name || `Cut #${c.id}`}</span>
                              <span className="cut-meta">{formatDuration(c.duration)} · {formatLabel(c.format)}</span>
                              <span className="cut-add">+</span>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                    <div className="cuts-panel">
                      <h4>No job (ordem)</h4>
                      <div className="cuts-list cuts-selected">
                        {selectedCuts.length === 0 ? (
                          <p className="cuts-empty">Clique nos cortes à esquerda para adicionar</p>
                        ) : (
                          selectedCuts.map((cutId, idx) => {
                            const c = cuts.find((x) => x.id === cutId)
                            if (!c) return null
                            return (
                              <div key={c.id} className="cuts-list-item selected">
                                <span className="cut-order">{idx + 1}</span>
                                <span className="cut-name">{c.name || `Cut #${c.id}`}</span>
                                <span className="cut-meta">{formatDuration(c.duration)}</span>
                                <div className="cut-actions">
                                  <button type="button" onClick={() => moveCutUp(idx)} title="Subir">↑</button>
                                  <button type="button" onClick={() => moveCutDown(idx)} title="Descer">↓</button>
                                  <button type="button" onClick={() => removeCutFromJob(c.id)} title="Remover">✕</button>
                                </div>
                              </div>
                            )
                          })
                        )}
                      </div>
                    </div>
                  </div>
                </div>
                <div className="cuts-modal-footer">
                  <button type="button" className="btn-close-modal" onClick={() => setCutsModalOpen(false)}>
                    Concluir
                  </button>
                </div>
              </div>
            </div>
          )}
          <div className="form-group">
            <label>Outro / CTA</label>
            <select value={outroAsset || ''} onChange={(e) => setOutroAsset(e.target.value || null)}>
              <option value="">Nenhum</option>
              {outros.map((a) => (
                <option key={a.id} value={a.id}>{a.label || a.asset_type}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Formato</label>
            <select value={makeVertical ? 'vertical' : 'horizontal'} onChange={(e) => setMakeVertical(e.target.value === 'vertical')}>
              <option value="vertical">Vertical (9:16)</option>
              <option value="horizontal">Horizontal (16:9)</option>
            </select>
          </div>
          <div className="form-group">
            <label>Transição</label>
            <select value={transition} onChange={(e) => setTransition(e.target.value)}>
              <option value="none">Nenhuma</option>
              <option value="fade">Fade</option>
              <option value="fadeblack">Fade por preto</option>
              <option value="wipeleft">Wipe esquerda</option>
              <option value="wiperight">Wipe direita</option>
              <option value="dissolve">Dissolve</option>
            </select>
          </div>
          {transition !== 'none' && (
            <div className="form-group">
              <label>Duração transição (s)</label>
              <input
                type="number"
                step="0.1"
                value={transitionDuration}
                onChange={(e) => setTransitionDuration(parseFloat(e.target.value) || 0.5)}
              />
            </div>
          )}
          <button type="submit" disabled={creating}>
            {creating ? 'Criando...' : 'Adicionar na fila'}
          </button>
        </form>
      </section>

      {queueJobs.length > 0 && (
        <section className="section">
          <h2>Na fila</h2>
          <div className="jobs-grid">
            {queueJobs.map((job) => (
              <div key={job.id} className="job-card">
                <span className="job-name">{job.name || `Job #${job.id}`}</span>
                <span className="job-status" data-status={job.status}>
                  {statusLabel[job.status]}
                </span>
                {job.status === 'RUNNING' && (
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${job.progress || 0}%` }} />
                  </div>
                )}
                {(job.status === 'QUEUED' || job.status === 'FAILED') && (
                  <button
                    type="button"
                    className="btn-run"
                    onClick={() => handleRunJob(job)}
                    disabled={runningJobId === job.id}
                  >
                    {runningJobId === job.id ? 'Enviando...' : 'Executar'}
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="section">
        <h2>Vídeos finalizados</h2>
        {finishedJobs.length === 0 ? (
          <p className="empty-msg">Nenhum vídeo finalizado.</p>
        ) : (
          <div className="jobs-grid">
            {finishedJobs.map((job) => (
              <div key={job.id} className="job-card finished">
                <span className="job-name">{job.name || `Job #${job.id}`}</span>
                <span className="job-status" data-status={job.status}>
                  {statusLabel[job.status]}
                </span>
                <span className="job-date">{new Date(job.created_at).toLocaleDateString('pt-BR')}</span>
                <span className="job-format">{job.make_vertical ? 'Vertical' : 'Horizontal'}</span>
                {job.subtitle_status === 'generating' && <p className="job-subtitle-status">Gerando legendas...</p>}
                {job.subtitle_status === 'burning' && <p className="job-subtitle-status">Finalizando legenda</p>}
                {job.subtitle_status === 'error' && job.subtitle_error && <p className="job-error">{job.subtitle_error}</p>}
                {job.status === 'FAILED' && <p className="job-error">{job.error}</p>}
                {job.status === 'DONE' && job.output_url && (
                  <div className="job-subtitle-buttons">
                    {(!job.subtitle_status || job.subtitle_status === 'error') && (
                      <button type="button" className="btn-subtitle" onClick={() => handleGenerateSubtitles(job)}>
                        Gerar legenda
                      </button>
                    )}
                    {(job.subtitle_status === 'ready_for_edit' || job.subtitle_status === 'burned') && (
                      <button type="button" className="btn-subtitle" onClick={() => handleOpenSubtitleModal(job)}>
                        Editar legendas
                      </button>
                    )}
                  </div>
                )}
                <div className="job-card-actions">
                  {job.status === 'DONE' && job.output_url && (
                    <button
                      type="button"
                      className="btn-download"
                      onClick={() => downloadJobVideo(job.id, job.name).catch((e) => setError(e.message))}
                    >
                      Baixar
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-delete"
                    onClick={() => handleDelete(job)}
                    disabled={!job.can_delete}
                    title={!job.can_delete ? 'Há agendamento pendente' : ''}
                  >
                    Deletar
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {subtitleModalJob && (
        <SubtitleEditorModal
          job={subtitleModalJob}
          onClose={() => setSubtitleModalJob(null)}
          onSave={handleSaveSubtitles}
          onBurn={handleBurnSubtitles}
        />
      )}
    </div>
  )
}

const DEFAULT_STYLE = { font: 'Arial', size: 24, color: '#FFFFFF', outline_color: '#000000', position: 'bottom', animated: false }
const FONTS = ['Arial', 'Helvetica', 'Open Sans', 'Roboto', 'Verdana']
const POSITIONS = [{ id: 'bottom', label: 'Inferior' }, { id: 'center', label: 'Centro' }, { id: 'top', label: 'Superior' }]

function SubtitleEditorModal({ job, onClose, onSave, onBurn }) {
  const [segments, setSegments] = useState(job.subtitle_segments || [])
  const [style, setStyle] = useState({ ...DEFAULT_STYLE, ...(job.subtitle_style || {}) })
  const [saving, setSaving] = useState(false)
  const [burning, setBurning] = useState(false)

  useEffect(() => {
    setSegments(job.subtitle_segments || [])
    setStyle({ ...DEFAULT_STYLE, ...(job.subtitle_style || {}) })
  }, [job.id])

  function secToTc(sec) {
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    const s = Math.floor(sec % 60)
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  function updateSegment(i, text) {
    setSegments((prev) => prev.map((s, idx) => (idx === i ? { ...s, text } : s)))
  }

  async function handleSave() {
    setSaving(true)
    try {
      await onSave(segments, style)
    } finally {
      setSaving(false)
    }
  }

  async function handleBurn() {
    setBurning(true)
    try {
      await onSave(segments, style)
      await onBurn()
      onClose()
    } catch (e) {
      setBurning(false)
    }
  }

  return (
    <div className="modal-overlay subtitle-modal-overlay" onClick={onClose}>
      <div className="subtitle-modal" onClick={(e) => e.stopPropagation()}>
        <div className="subtitle-modal-header">
          <h3>Editar legendas – {job.name || `Job #${job.id}`}</h3>
          <button type="button" className="subtitle-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="subtitle-modal-body">
          <div className="subtitle-style-section">
            <h4>Estilo das legendas</h4>
            <div className="subtitle-style-grid">
              <div className="form-group">
                <label>Fonte</label>
                <select value={style.font} onChange={(e) => setStyle((s) => ({ ...s, font: e.target.value }))}>
                  {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>Tamanho</label>
                <input type="number" min="16" max="48" value={style.size} onChange={(e) => setStyle((s) => ({ ...s, size: parseInt(e.target.value, 10) || 24 }))} />
              </div>
              <div className="form-group">
                <label>Cor do texto</label>
                <input type="color" value={style.color} onChange={(e) => setStyle((s) => ({ ...s, color: e.target.value }))} />
                <span className="color-hex">{style.color}</span>
              </div>
              <div className="form-group">
                <label>Cor da borda</label>
                <input type="color" value={style.outline_color} onChange={(e) => setStyle((s) => ({ ...s, outline_color: e.target.value }))} />
                <span className="color-hex">{style.outline_color}</span>
              </div>
              <div className="form-group">
                <label>Posição</label>
                <select value={style.position} onChange={(e) => setStyle((s) => ({ ...s, position: e.target.value }))}>
                  {POSITIONS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
              <div className="form-group form-group-checkbox">
                <label>
                  <input
                    type="checkbox"
                    checked={!!style.animated}
                    onChange={(e) => setStyle((s) => ({ ...s, animated: e.target.checked }))}
                  />
                  {' '}Legendas animadas (palavra por palavra)
                </label>
                <span className="form-hint">As palavras aparecem no vídeo conforme são faladas</span>
              </div>
            </div>
          </div>
          <div className="subtitle-segments-section">
            <h4>Texto</h4>
            <div className="subtitle-segments-list">
              {segments.map((seg, i) => (
                <div key={i} className="subtitle-segment">
                  <span className="subtitle-segment-time">{secToTc(seg.start)} → {secToTc(seg.end)}</span>
                  <textarea
                    value={seg.text}
                    onChange={(e) => updateSegment(i, e.target.value)}
                    rows={2}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="subtitle-modal-footer">
          <button type="button" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn-save-subtitles" onClick={handleSave} disabled={saving}>
            {saving ? 'Salvando...' : 'Salvar alterações'}
          </button>
          <button type="button" className="btn-burn-subtitles" onClick={handleBurn} disabled={burning}>
            {burning ? 'Finalizando...' : 'Finalizar'}
          </button>
        </div>
      </div>
    </div>
  )
}
