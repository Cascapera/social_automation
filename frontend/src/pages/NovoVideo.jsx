import { useState, useEffect } from 'react'
import {
  getBrands,
  getBrandAssets,
  uploadSource,
  createCuts,
  createJob,
  runJob,
  getJob,
  createScheduledPost,
  createBrand,
  downloadJobVideo,
} from '../api'
import './NovoVideo.css'

const STEPS = ['Upload', 'Cortes', 'Job', 'Processar', 'Agendar']
const PLATFORMS = [
  { id: 'IG', label: 'Instagram Reels' },
  { id: 'TT', label: 'TikTok' },
  { id: 'YT', label: 'YouTube Shorts' },
  { id: 'YTB', label: 'YouTube' },
]

export default function NovoVideo() {
  const [step, setStep] = useState(0)
  const [brands, setBrands] = useState([])
  const [intros, setIntros] = useState([])
  const [outros, setOutros] = useState([])  // inclui OUTRO e CTA

  // Step 0: Upload
  const [brandId, setBrandId] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [source, setSource] = useState(null)

  // Step 1: Cuts
  const [cuts, setCuts] = useState([{ name: '', start_tc: '', end_tc: '' }])
  const [savingCuts, setSavingCuts] = useState(false)

  // Step 2: Job
  const [jobName, setJobName] = useState('')
  const [cutIds, setCutIds] = useState([])
  const [targetPlatforms, setTargetPlatforms] = useState(['YT'])
  const [makeVertical, setMakeVertical] = useState(true)
  const [introAsset, setIntroAsset] = useState(null)
  const [outroAsset, setOutroAsset] = useState(null)
  const [transition, setTransition] = useState('none')
  const [transitionDuration, setTransitionDuration] = useState(0.5)
  const [creatingJob, setCreatingJob] = useState(false)
  const [job, setJob] = useState(null)

  // Step 3: Process
  const [running, setRunning] = useState(false)
  const [polling, setPolling] = useState(false)

  // Step 4: Schedule
  const [scheduledPlatforms, setScheduledPlatforms] = useState([])
  const [scheduledAt, setScheduledAt] = useState('')
  const [scheduling, setScheduling] = useState(false)
  const [scheduled, setScheduled] = useState(false)

  const [error, setError] = useState('')
  const [newBrandName, setNewBrandName] = useState('')
  const [creatingBrand, setCreatingBrand] = useState(false)
  const [showNewBrand, setShowNewBrand] = useState(false)

  useEffect(() => {
    getBrands().then(setBrands).catch(() => setBrands([]))
  }, [])

  useEffect(() => {
    if (brandId) {
      getBrandAssets(brandId, 'INTRO').then(setIntros).catch(() => setIntros([]))
      Promise.all([
        getBrandAssets(brandId, 'OUTRO').catch(() => []),
        getBrandAssets(brandId, 'CTA').catch(() => []),
      ]).then(([outrosList, ctaList]) => setOutros([...outrosList, ...ctaList]))
    } else {
      setIntros([])
      setOutros([])
    }
  }, [brandId])

  async function handleCreateBrand(e) {
    e.preventDefault()
    if (!newBrandName.trim()) {
      setError('Digite o nome da marca')
      return
    }
    setError('')
    setCreatingBrand(true)
    try {
      const b = await createBrand(newBrandName.trim())
      setBrands((prev) => [...prev, b])
      setBrandId(String(b.id))
      setNewBrandName('')
      setShowNewBrand(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setCreatingBrand(false)
    }
  }

  async function handleUpload(e) {
    e.preventDefault()
    if (!file || !brandId || !title) {
      setError('Preencha marca, título e selecione o arquivo')
      return
    }
    setError('')
    setUploading(true)
    try {
      const s = await uploadSource(brandId, title, file)
      setSource(s)
      setStep(1)
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  function addCut() {
    setCuts([...cuts, { name: '', start_tc: '', end_tc: '' }])
  }

  function removeCut(i) {
    setCuts(cuts.filter((_, idx) => idx !== i))
  }

  function updateCut(i, field, value) {
    const next = [...cuts]
    next[i] = { ...next[i], [field]: value }
    setCuts(next)
  }

  async function handleSaveCuts(e) {
    e.preventDefault()
    const valid = cuts.filter((c) => c.start_tc && c.end_tc)
    if (valid.length === 0 || !source) {
      setError('Adicione pelo menos um corte com start e end')
      return
    }
    setError('')
    setSavingCuts(true)
    try {
      const created = await createCuts(
        source.id,
        valid.map((c) => ({ name: c.name || '', start_tc: c.start_tc, end_tc: c.end_tc }))
      )
      setCutIds(created.map((c) => c.id))
      setStep(2)
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingCuts(false)
    }
  }

  function togglePlatform(id) {
    setTargetPlatforms((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    )
  }

  async function handleCreateJob(e) {
    e.preventDefault()
    if (cutIds.length === 0) {
      setError('Nenhum corte selecionado')
      return
    }
    if (targetPlatforms.length === 0) {
      setError('Selecione pelo menos uma rede')
      return
    }
    setError('')
    setCreatingJob(true)
    try {
      const j = await createJob({
        name: jobName.trim() || undefined,
        cut_ids: cutIds,
        target_platforms: targetPlatforms,
        make_vertical: makeVertical,
        intro_asset: introAsset || null,
        outro_asset: outroAsset || null,
        transition,
        transition_duration: transitionDuration,
      })
      setJob(j)
      setStep(3)
    } catch (err) {
      setError(err.message)
    } finally {
      setCreatingJob(false)
    }
  }

  async function handleRun() {
    if (!job) return
    setError('')
    setRunning(true)
    try {
      await runJob(job.id)
      setPolling(true)
    } catch (err) {
      setError(err.message)
      setRunning(false)
    }
  }

  useEffect(() => {
    if (!polling || !job) return
    const id = setInterval(async () => {
      try {
        const j = await getJob(job.id)
        setJob(j)
        if (j.status === 'DONE' || j.status === 'FAILED') {
          setPolling(false)
          setRunning(false)
        }
      } catch {
        setPolling(false)
        setRunning(false)
      }
    }, 2000)
    return () => clearInterval(id)
  }, [polling, job?.id])

  async function handleSchedule(e) {
    e.preventDefault()
    if (scheduledPlatforms.length === 0 || !scheduledAt) {
      setError('Selecione plataformas e data/hora')
      return
    }
    setError('')
    setScheduling(true)
    try {
      await createScheduledPost(job.id, scheduledPlatforms, scheduledAt)
      setScheduled(true)
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setScheduling(false)
    }
  }

  function toggleScheduledPlatform(id) {
    setScheduledPlatforms((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    )
  }

  return (
    <div className="novo-video">
      <h1>Novo vídeo</h1>
      <div className="steps">
        {STEPS.map((s, i) => (
          <div
            key={s}
            className={`step-indicator ${i <= step ? 'active' : ''} ${i === step ? 'current' : ''}`}
          >
            {i + 1}. {s}
          </div>
        ))}
      </div>

      {error && <div className="form-error">{error}</div>}

      {/* Step 0: Upload */}
      {step === 0 && (
        <form onSubmit={handleUpload} className="step-form">
          <div className="form-group">
            <label>Marca</label>
            <select value={brandId} onChange={(e) => setBrandId(e.target.value)} required>
              <option value="">Selecione</option>
              {brands.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
            {!showNewBrand ? (
              <button type="button" className="btn-link" onClick={() => setShowNewBrand(true)}>+ Nova marca</button>
            ) : (
              <form onSubmit={handleCreateBrand} className="new-brand-form">
                <input
                  type="text"
                  value={newBrandName}
                  onChange={(e) => setNewBrandName(e.target.value)}
                  placeholder="Nome da marca"
                  autoFocus
                />
                <button type="submit" disabled={creatingBrand}>{creatingBrand ? 'Criando...' : 'Criar'}</button>
                <button type="button" onClick={() => { setShowNewBrand(false); setNewBrandName('') }}>Cancelar</button>
              </form>
            )}
          </div>
          <div className="form-group">
            <label>Título</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Ex: Live 18/02"
              required
            />
          </div>
          <div className="form-group">
            <label>Vídeo (MP4)</label>
            <input
              type="file"
              accept="video/mp4,video/*"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              required
            />
          </div>
          <button type="submit" disabled={uploading}>
            {uploading ? 'Enviando...' : 'Enviar e continuar'}
          </button>
        </form>
      )}

      {/* Step 1: Cuts */}
      {step === 1 && (
        <form onSubmit={handleSaveCuts} className="step-form">
          <p className="step-desc">Defina os cortes (formato: HH:MM:SS ou HH:MM:SS.ms)</p>
          {cuts.map((c, i) => (
            <div key={i} className="cut-row">
              <input
                placeholder="Nome (opcional)"
                value={c.name}
                onChange={(e) => updateCut(i, 'name', e.target.value)}
              />
              <input
                placeholder="Início (ex: 00:01:30)"
                value={c.start_tc}
                onChange={(e) => updateCut(i, 'start_tc', e.target.value)}
              />
              <input
                placeholder="Fim (ex: 00:02:00)"
                value={c.end_tc}
                onChange={(e) => updateCut(i, 'end_tc', e.target.value)}
              />
              <button type="button" onClick={() => removeCut(i)} className="btn-remove">
                ✕
              </button>
            </div>
          ))}
          <button type="button" onClick={addCut} className="btn-add">+ Adicionar corte</button>
          <button type="submit" disabled={savingCuts}>
            {savingCuts ? 'Salvando...' : 'Salvar e continuar'}
          </button>
        </form>
      )}

      {/* Step 2: Job */}
      {step === 2 && (
        <form onSubmit={handleCreateJob} className="step-form">
          <div className="form-group">
            <label>Nome do job</label>
            <input
              type="text"
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
              placeholder="Ex: Live 18/02 - YouTube"
            />
            <span className="form-hint">Para localizar facilmente no dashboard</span>
          </div>
          <div className="form-group">
            <label>Redes para publicar</label>
            <div className="platforms">
              {PLATFORMS.map((p) => (
                <label key={p.id} className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={targetPlatforms.includes(p.id)}
                    onChange={() => togglePlatform(p.id)}
                  />
                  {p.label}
                </label>
              ))}
            </div>
          </div>
          <div className="form-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={makeVertical}
                onChange={(e) => setMakeVertical(e.target.checked)}
              />
              Formato vertical (9:16)
            </label>
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
            <label>Outro / CTA</label>
            <select value={outroAsset || ''} onChange={(e) => setOutroAsset(e.target.value || null)}>
              <option value="">Nenhum</option>
              {outros.map((a) => (
                <option key={a.id} value={a.id}>{a.label || a.asset_type}</option>
              ))}
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
              <label>Duração da transição (s)</label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                value={transitionDuration}
                onChange={(e) => setTransitionDuration(parseFloat(e.target.value) || 0.5)}
              />
            </div>
          )}
          <button type="submit" disabled={creatingJob}>
            {creatingJob ? 'Criando...' : 'Criar job'}
          </button>
        </form>
      )}

      {/* Step 3: Process */}
      {step === 3 && job && (
        <div className="step-form">
          <div className="job-status-card">
            <p><strong>Status:</strong> {job.status}</p>
            {job.status === 'RUNNING' && (
              <div className="progress-bar">
                <div className="progress-fill" style={{ width: `${job.progress || 0}%` }} />
              </div>
            )}
            {job.status === 'DONE' && job.output_url && (
              <button
                type="button"
                className="btn-download"
                onClick={() => downloadJobVideo(job.id, job.name).catch((e) => setError(e.message))}
              >
                Baixar vídeo final
              </button>
            )}
            {job.status === 'FAILED' && <p className="job-error">{job.error}</p>}
          </div>
          {!running && !polling && job.status === 'QUEUED' && (
            <button onClick={handleRun} className="btn-primary">
              Iniciar processamento
            </button>
          )}
          {(job.status === 'DONE' || job.status === 'FAILED') && (
            <button onClick={() => setStep(4)} className="btn-secondary">
              Ir para agendamento
            </button>
          )}
        </div>
      )}

      {/* Step 4: Schedule */}
      {step === 4 && job && !scheduled && (
        <form onSubmit={handleSchedule} className="step-form">
          <p className="step-desc">Agende a postagem do vídeo nas redes</p>
          <div className="form-group">
            <label>Redes</label>
            <div className="platforms">
              {PLATFORMS.filter((p) => job.target_platforms?.includes(p.id)).map((p) => (
                <label key={p.id} className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={scheduledPlatforms.includes(p.id)}
                    onChange={() => toggleScheduledPlatform(p.id)}
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
          <button type="submit" disabled={scheduling}>
            {scheduling ? 'Agendando...' : 'Agendar postagem'}
          </button>
        </form>
      )}

      {step === 4 && scheduled && (
        <div className="step-form">
          <p className="success-msg">Agendamento concluído! O vídeo será postado na data escolhida.</p>
        </div>
      )}
    </div>
  )
}
