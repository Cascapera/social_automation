import { useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getAutoCutAnalyses,
  getAutoCutAnalysis,
  createAutoCutAnalysis,
  getSources,
  getBrandAssets,
  getBrandSocialAccounts,
  resetStuckAutoCuts,
  deleteStuckAutoCuts,
  updateAutoCutCorte,
  finalizarAutoCutJob,
  deleteAutoCutAnalysis,
  bulkScheduleAutoCutAnalysis,
  scheduleAutoCutCorte,
  getAutoCutCortes,
  deleteAutoCutCorte,
  uploadAutoCutCorteThumbnail,
} from '../api'
import './CortesAutomaticos.css'

function formatDuration(sec) {
  if (sec == null) return '-'
  if (sec >= 60) return `${Math.floor(sec / 60)}min ${Math.floor(sec % 60)}s`
  return `${Math.floor(sec)}s`
}

function formatDurationMin(min) {
  if (min == null) return '-'
  return `${min}min`
}

function isShortCorte(corte) {
  const s = corte?.suggestion
  if (s?.duration_seconds != null) return s.duration_seconds <= 180
  if (s?.duration_minutes != null) return s.duration_minutes <= 3
  return corte?.suggestion?.cut_type === 'short'
}

export default function CortesAutomaticos() {
  const { brandId, brands } = useBrand()
  const [analyses, setAnalyses] = useState([])
  const [sources, setSources] = useState([])
  const [socialAccounts, setSocialAccounts] = useState([])
  const [expandedId, setExpandedId] = useState(null)
  const [file, setFile] = useState(null)
  const [sourceId, setSourceId] = useState('')
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [name, setName] = useState('')
  const [assunto, setAssunto] = useState('')
  const [convidados, setConvidados] = useState('')
  const [promptVersion, setPromptVersion] = useState('educational')
  const [thumbnailFont, setThumbnailFont] = useState('impact')
  const [thumbnailBandColor, setThumbnailBandColor] = useState('#E12E20')
  const [thumbnailTextColor, setThumbnailTextColor] = useState('#0A0A0A')
  const [thumbnailStrokeColor, setThumbnailStrokeColor] = useState('#FFEBDC')
  const [shortsTarget, setShortsTarget] = useState(12)
  const [longsTarget, setLongsTarget] = useState(3)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [resettingStuck, setResettingStuck] = useState(false)
  const [deletingStuck, setDeletingStuck] = useState(false)
  const [finalizing, setFinalizing] = useState(null)
  const [deletingJob, setDeletingJob] = useState(null)
  const [finalizedCortes, setFinalizedCortes] = useState([])
  const [filters, setFilters] = useState({ date_from: '', date_to: '', format: '' })
  const [subtitleStyle, setSubtitleStyle] = useState({
    font: 'Helvetica',
    size: 24,
    color: '#FFFFFF',
    outline_color: '#000000',
  })
  const [verticalOptions, setVerticalOptions] = useState({
    mode: 'frame_center',
    background_color: '#000000',
    custom_text: '',
    font_size_title: 36,
    font_size_text: 28,
    title_color: '#FFFFFF',
    text_color: '#FFFFFF',
  })
  const [horizontalOptions, setHorizontalOptions] = useState({
    insert_logo: false,
    logo_x: 100,
    logo_y: 100,
  })
  const [animationAssets, setAnimationAssets] = useState([])
  const [overlayAnimationOptions, setOverlayAnimationOptions] = useState({
    asset_id: '',
    position: 'bottom_right',
    margin: 24,
    height: 120,
  })
  const [editLegendaCorte, setEditLegendaCorte] = useState(null)
  const [confirmDeleteJob, setConfirmDeleteJob] = useState(null)
  const [expandedFinalizedId, setExpandedFinalizedId] = useState(null)
  const [deletingFinalizedJob, setDeletingFinalizedJob] = useState(null)
  const [confirmDeleteFinalizedJob, setConfirmDeleteFinalizedJob] = useState(null)
  const [bulkPublishModal, setBulkPublishModal] = useState(null)
  const [bulkPublishing, setBulkPublishing] = useState(false)
  const [unitScheduleModal, setUnitScheduleModal] = useState(null)
  const [unitScheduling, setUnitScheduling] = useState(false)
  const [uploadingThumbnailId, setUploadingThumbnailId] = useState(null)
  const [thumbnailModal, setThumbnailModal] = useState(null)

  const selectedAnalysis = analyses.find((a) => a.id === expandedId)

  function buildDescriptionBody(analysis, brandExtra = '') {
    if (!analysis) return ''
    const videoName = (analysis.name || '').trim() || 'Vídeo original'
    const convidados = (analysis.convidados || '').trim() || '-'
    const lines = [
      `🎙️ Corte da live: ${videoName}`,
      '',
      `Convidado: ${convidados}`,
    ]
    const youtubeUrl = (analysis.youtube_url || '').trim()
    if (youtubeUrl) {
      lines.push('', '📺 Episódio completo:', youtubeUrl)
    }
    const autoPart = lines.join('\n').trim()
    const extra = (brandExtra || '').trim()
    return extra ? `${autoPart}\n\n\n${extra}` : autoPart
  }

  function buildBulkDescriptionPreview(analysis, brandExtra = '') {
    return `{titulo do video}\n\n${buildDescriptionBody(analysis, brandExtra)}`
  }

  function buildUnitDescriptionPreview(analysis, title, brandExtra = '') {
    const safeTitle = (title || '').trim() || '{titulo do video}'
    return `${safeTitle}\n\n${buildDescriptionBody(analysis, brandExtra)}`
  }

  useEffect(() => {
    if (brandId) {
      getAutoCutAnalyses(brandId, { excludeFinalized: true }).then(setAnalyses).catch(() => setAnalyses([]))
      getSources(brandId).then(setSources).catch(() => setSources([]))
      getBrandSocialAccounts(brandId).then(setSocialAccounts).catch(() => setSocialAccounts([]))
      getAutoCutCortes(brandId, { finalized: true, ...filters }).then(setFinalizedCortes).catch(() => setFinalizedCortes([]))
      getBrandAssets(brandId, 'ANIMATION').then(setAnimationAssets).catch(() => setAnimationAssets([]))
    } else {
      setAnalyses([])
      setSocialAccounts([])
      setFinalizedCortes([])
    }
  }, [brandId, filters])

  useEffect(() => {
    if (!selectedAnalysis) return
    const id = setInterval(async () => {
      try {
        const a = await getAutoCutAnalysis(selectedAnalysis.id)
        setAnalyses((prev) => prev.map((x) => (x.id === a.id ? a : x)))
        if (expandedId === a.id) setExpandedId(a.id)
        if (a.status === 'done' || a.status === 'error') clearInterval(id)
      } catch {
        clearInterval(id)
      }
    }, 3000)
    return () => clearInterval(id)
  }, [selectedAnalysis?.id, selectedAnalysis?.status, expandedId])

  async function handleGenerate(e) {
    e.preventDefault()
    if (!brandId) {
      setError('Selecione uma marca no menu à esquerda')
      return
    }
    if (!file && !sourceId && !youtubeUrl) {
      setError('Envie um vídeo, selecione um source ou informe um URL do YouTube')
      return
    }
    setError('')
    setCreating(true)
    try {
      const a = await createAutoCutAnalysis({
        file: file || undefined,
        sourceId: sourceId || undefined,
        youtubeUrl: youtubeUrl || undefined,
        brandId,
        name: name || undefined,
        assunto: assunto || undefined,
        convidados: convidados || undefined,
        promptVersion: promptVersion || undefined,
        thumbnailFont: thumbnailFont || undefined,
        thumbnailBandColor: thumbnailBandColor || undefined,
        thumbnailTextColor: thumbnailTextColor || undefined,
        thumbnailStrokeColor: thumbnailStrokeColor || undefined,
        shortsTarget,
        longsTarget,
      })
      setAnalyses((prev) => [a, ...prev])
      setExpandedId(a.id)
      setFile(null)
      setSourceId('')
      setYoutubeUrl('')
      setName('')
      setAssunto('')
      setConvidados('')
      setPromptVersion('educational')
      setThumbnailFont('impact')
      setThumbnailBandColor('#E12E20')
      setThumbnailTextColor('#0A0A0A')
      setThumbnailStrokeColor('#FFEBDC')
      setShortsTarget(12)
      setLongsTarget(3)
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  async function handleResetStuck() {
    if (!brandId) return
    setResettingStuck(true)
    setError('')
    try {
      const res = await resetStuckAutoCuts(brandId)
      if (res.reset > 0) {
        const list = await getAutoCutAnalyses(brandId, { excludeFinalized: true })
        setAnalyses(list)
        setError(`${res.reset} análise(s) travada(s) limpa(s).`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setResettingStuck(false)
    }
  }

  async function handleDeleteStuck() {
    if (!brandId) return
    setDeletingStuck(true)
    setError('')
    try {
      const res = await deleteStuckAutoCuts(brandId)
      if (res.deleted > 0) {
        const list = await getAutoCutAnalyses(brandId, { excludeFinalized: true })
        setAnalyses(list)
        if (expandedId && !list.find((a) => a.id === expandedId)) setExpandedId(null)
        setError(`${res.deleted} job(s) interrompido(s) deletado(s).`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setDeletingStuck(false)
    }
  }

  async function handleCorteChange(corte, field, value) {
    try {
      await updateAutoCutCorte(corte.id, { [field]: value })
      const a = await getAutoCutAnalysis(selectedAnalysis.id)
      setAnalyses((prev) => prev.map((x) => (x.id === a.id ? a : x)))
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleSaveLegenda(corte, segments) {
    try {
      await updateAutoCutCorte(corte.id, { subtitle_segments: segments })
      const a = await getAutoCutAnalysis(selectedAnalysis.id)
      setAnalyses((prev) => prev.map((x) => (x.id === a.id ? a : x)))
      setEditLegendaCorte(null)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleFinalizar() {
    if (!selectedAnalysis) return
    setFinalizing(selectedAnalysis.id)
    setError('')
    try {
      await finalizarAutoCutJob(selectedAnalysis.id, {
        subtitle_style: subtitleStyle,
        vertical_mode: verticalOptions.mode,
        background_color: verticalOptions.background_color,
        custom_text: verticalOptions.custom_text,
        font_size_title: verticalOptions.font_size_title,
        font_size_text: verticalOptions.font_size_text,
        title_color: verticalOptions.title_color,
        text_color: verticalOptions.text_color,
        horizontal_insert_logo: horizontalOptions.insert_logo,
        horizontal_logo_x: horizontalOptions.insert_logo ? horizontalOptions.logo_x : undefined,
        horizontal_logo_y: horizontalOptions.insert_logo ? horizontalOptions.logo_y : undefined,
        overlay_animation_asset_id: overlayAnimationOptions.asset_id ? Number(overlayAnimationOptions.asset_id) : undefined,
        overlay_position: overlayAnimationOptions.asset_id ? overlayAnimationOptions.position : undefined,
        overlay_margin: overlayAnimationOptions.asset_id ? overlayAnimationOptions.margin : undefined,
        overlay_height: overlayAnimationOptions.asset_id ? overlayAnimationOptions.height : undefined,
      })
      const list = await getAutoCutAnalyses(brandId, { excludeFinalized: true })
      setAnalyses(list)
      setExpandedId(null)
      getAutoCutCortes(brandId, { finalized: true, ...filters }).then(setFinalizedCortes)
      setError('Cortes em finalização (queima de legendas em background). Atualize a página para ver os resultados.')
    } catch (e) {
      setError(e.message)
    } finally {
      setFinalizing(null)
    }
  }

  async function handleDeleteJob(analysis) {
    setDeletingJob(analysis.id)
    setConfirmDeleteJob(null)
    try {
      await deleteAutoCutAnalysis(analysis.id)
      setAnalyses((prev) => prev.filter((a) => a.id !== analysis.id))
      if (expandedId === analysis.id) setExpandedId(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setDeletingJob(null)
    }
  }

  async function handleDeleteCorte(corte) {
    try {
      await deleteAutoCutCorte(corte.id, brandId)
      setFinalizedCortes((prev) => prev.filter((c) => c.id !== corte.id))
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleReplaceThumbnail(corte, file) {
    if (!file) return
    const maxSize = 2 * 1024 * 1024
    if (file.size > maxSize) {
      setError('A capa deve ter no máximo 2MB (limite do YouTube).')
      return
    }
    setUploadingThumbnailId(corte.id)
    setError('')
    try {
      await uploadAutoCutCorteThumbnail(corte.id, file)
      const refreshed = await getAutoCutCortes(brandId, { finalized: true, ...filters })
      setFinalizedCortes(refreshed)
      const updated = refreshed.find((x) => x.id === corte.id)
      if (updated) setThumbnailModal(updated)
    } catch (err) {
      setError(err.message)
    } finally {
      setUploadingThumbnailId(null)
    }
  }

  async function handleDeleteFinalizedJob(analysisId) {
    setDeletingFinalizedJob(analysisId)
    setConfirmDeleteFinalizedJob(null)
    try {
      await deleteAutoCutAnalysis(analysisId)
      setFinalizedCortes((prev) => prev.filter((c) => c.analysis_id !== analysisId))
      if (expandedFinalizedId === analysisId) setExpandedFinalizedId(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setDeletingFinalizedJob(null)
    }
  }

  async function handleOpenBulkPublish(analysisId, name) {
    const now = new Date()
    const plusOneHour = new Date(now.getTime() + 60 * 60 * 1000)
    const toLocalInput = (d) => {
      const pad = (n) => String(n).padStart(2, '0')
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
    }
    const selectedBrand = brands.find((b) => String(b.id) === String(brandId))
    const brandExtra = selectedBrand?.youtube_description_extra || ''
    let preview = '{titulo do video}'
    try {
      const analysis = await getAutoCutAnalysis(analysisId)
      preview = buildBulkDescriptionPreview(analysis, brandExtra)
    } catch {
      preview = `{titulo do video}\n\n(Descrição padrão não pôde ser carregada no momento.)`
    }
    setBulkPublishModal({
      analysisId,
      name,
      startAt: toLocalInput(now),
      endAt: toLocalInput(plusOneHour),
      privacyStatus: 'private',
      socialAccountId: '',
      descriptionPreview: preview,
    })
  }

  async function handleBulkPublishSubmit(e) {
    e.preventDefault()
    if (!bulkPublishModal) return
    if (!bulkPublishModal.startAt || !bulkPublishModal.endAt) {
      setError('Informe início e fim da janela')
      return
    }
    const youtubeAccounts = socialAccounts.filter((a) => a.platform === 'YTB' || a.platform === 'YT')
    if (youtubeAccounts.length > 1 && !bulkPublishModal.socialAccountId) {
      setError('Selecione o canal YouTube para o agendamento em massa.')
      return
    }
    setBulkPublishing(true)
    setError('')
    try {
      const res = await bulkScheduleAutoCutAnalysis(bulkPublishModal.analysisId, {
        startAt: bulkPublishModal.startAt,
        endAt: bulkPublishModal.endAt,
        privacyStatus: bulkPublishModal.privacyStatus,
        socialAccountId: bulkPublishModal.socialAccountId ? Number(bulkPublishModal.socialAccountId) : null,
      })
      setBulkPublishModal(null)
      setError(
        `Agendamento em massa concluído. Criados: ${res.created}, ignorados: ${res.skipped}. Curtos: ${res.short_count}, Longos: ${res.long_count}.`
      )
    } catch (e) {
      setError(e.message)
    } finally {
      setBulkPublishing(false)
    }
  }

  async function handleOpenUnitSchedule(corte) {
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    const toLocalInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
    const selectedBrand = brands.find((b) => String(b.id) === String(brandId))
    const brandExtra = selectedBrand?.youtube_description_extra || ''
    const title = corte.suggestion?.title || `Corte #${corte.id}`
    let preview = `${title}\n\n(Descrição padrão não pôde ser carregada no momento.)`
    try {
      const analysisId = corte.analysis_id ?? corte.analysis
      const analysis = await getAutoCutAnalysis(analysisId)
      preview = buildUnitDescriptionPreview(analysis, title, brandExtra)
    } catch {
      // mantém preview fallback
    }
    setUnitScheduleModal({
      corteId: corte.id,
      title,
      scheduledAt: toLocalInput(now),
      privacyStatus: 'private',
      descriptionPreview: preview,
    })
  }

  async function handleUnitScheduleSubmit(e) {
    e.preventDefault()
    if (!unitScheduleModal?.corteId || !unitScheduleModal?.scheduledAt) {
      setError('Informe a data/hora do agendamento')
      return
    }
    setUnitScheduling(true)
    setError('')
    try {
      const res = await scheduleAutoCutCorte(unitScheduleModal.corteId, {
        scheduledAt: unitScheduleModal.scheduledAt,
        privacyStatus: unitScheduleModal.privacyStatus,
      })
      setUnitScheduleModal(null)
      if (res?.skipped) {
        setError('Este corte já possui agendamento para a plataforma correspondente.')
      } else {
        setError(`Agendamento criado com sucesso para ${res.platform}.`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setUnitScheduling(false)
    }
  }

  const statusLabel = {
    pending: 'Pendente',
    transcribing: 'Transcrevendo',
    analyzing: 'Analisando',
    done: 'Concluído',
    error: 'Erro',
  }

  const cortes = selectedAnalysis?.cortes || []
  const shortCortes = cortes.filter(isShortCorte)
  const longCortes = cortes.filter((c) => !isShortCorte(c))

  return (
    <div className="cortes-automaticos">
      <h1>Cortes Automáticos</h1>
      <p className="page-desc">
        Envie um vídeo, cole um URL do YouTube ou selecione um source. O sistema transcreve, analisa com IA (Grok) e sugere cortes virais.
      </p>

      {error && <div className="form-error">{error}</div>}

      <section className="section">
        <h2>Gerar cortes</h2>
        <form onSubmit={handleGenerate} className="auto-cut-form">
          <div className="form-row">
            <div className="form-group">
              <label>Vídeo (upload)</label>
              <input
                type="file"
                accept="video/*"
                onChange={(e) => {
                  const f = e.target.files?.[0] || null
                  setFile(f)
                  if (f) { setSourceId(''); setYoutubeUrl('') }
                }}
              />
            </div>
            <div className="form-group">
              <label>Ou: Source existente</label>
              <select
                value={sourceId}
                onChange={(e) => {
                  const v = e.target.value
                  setSourceId(v)
                  if (v) { setFile(null); setYoutubeUrl('') }
                }}
              >
                <option value="">Selecione um source</option>
                {sources.map((s) => (
                  <option key={s.id} value={s.id}>{s.title || `Source #${s.id}`}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="form-group">
            <label>Ou: URL do YouTube</label>
            <input
              type="url"
              placeholder="https://www.youtube.com/watch?v=..."
              value={youtubeUrl}
              onChange={(e) => {
                const v = e.target.value.trim()
                setYoutubeUrl(v)
                if (v) { setFile(null); setSourceId('') }
              }}
            />
            <span className="form-hint">Cole o link do vídeo do YouTube. O vídeo será baixado automaticamente.</span>
          </div>
          <div className="form-group">
            <label>Nome (opcional)</label>
            <input
              type="text"
              placeholder="Deixe em branco para Job 1, 2, 3..."
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="form-group">
            <label>Assunto do vídeo (opcional)</label>
            <input
              type="text"
              placeholder="Ex: Inteligência artificial, carreira em tech"
              value={assunto}
              onChange={(e) => setAssunto(e.target.value)}
            />
          </div>
          <div className="form-group">
            <label>Convidados (opcional)</label>
            <input
              type="text"
              placeholder="Ex: João Silva, Maria Santos"
              value={convidados}
              onChange={(e) => setConvidados(e.target.value)}
            />
            <span className="form-hint">Se houver mais de um convidado, escreva os nomes separados por vírgula.</span>
          </div>
          <div className="form-group">
            <label>Modo de análise</label>
            <select
              value={promptVersion}
              onChange={(e) => setPromptVersion(e.target.value)}
            >
              <option value="educational">Educacional (PT, shorts 2–3 min)</option>
              <option value="viral">Viral (PT, shorts 30–60 seg)</option>
              <option value="educational_en">Educacional (EN, 2–3 min)</option>
              <option value="viral_en">Viral (EN, 30–60 seg)</option>
            </select>
            <span className="form-hint">PT: transcrição e títulos em português. EN: transcrição e títulos em inglês.</span>
          </div>
          <div className="form-group">
            <label>Fonte da thumbnail automática</label>
            <select
              value={thumbnailFont}
              onChange={(e) => setThumbnailFont(e.target.value)}
            >
              <option value="anton">Anton</option>
              <option value="bebas">Bebas Neue</option>
              <option value="montserrat">Montserrat ExtraBold</option>
              <option value="impact">Impact</option>
            </select>
            <span className="form-hint">Opções fixas: Anton, Bebas Neue, Montserrat ExtraBold, Impact.</span>
          </div>
          <div className="form-group">
            <label>Cor da faixa da thumbnail</label>
            <div className="color-input-row">
              <input
                type="color"
                value={thumbnailBandColor}
                onChange={(e) => setThumbnailBandColor(e.target.value.toUpperCase())}
              />
              <input
                type="text"
                value={thumbnailBandColor}
                onChange={(e) => setThumbnailBandColor(e.target.value)}
                className="color-hex"
                placeholder="#E12E20"
              />
            </div>
            <span className="form-hint">Padrão vermelho. Formato HEX: #RRGGBB.</span>
          </div>
          <div className="form-group">
            <label>Cor do texto da thumbnail</label>
            <div className="color-input-row">
              <input
                type="color"
                value={thumbnailTextColor}
                onChange={(e) => setThumbnailTextColor(e.target.value.toUpperCase())}
              />
              <input
                type="text"
                value={thumbnailTextColor}
                onChange={(e) => setThumbnailTextColor(e.target.value)}
                className="color-hex"
                placeholder="#0A0A0A"
              />
            </div>
            <span className="form-hint">Cor principal da letra.</span>
          </div>
          <div className="form-group">
            <label>Cor do efeito/contorno do texto</label>
            <div className="color-input-row">
              <input
                type="color"
                value={thumbnailStrokeColor}
                onChange={(e) => setThumbnailStrokeColor(e.target.value.toUpperCase())}
              />
              <input
                type="text"
                value={thumbnailStrokeColor}
                onChange={(e) => setThumbnailStrokeColor(e.target.value)}
                className="color-hex"
                placeholder="#FFEBDC"
              />
            </div>
            <span className="form-hint">Contorno para destacar a letra sobre a faixa.</span>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Qtd. shorts no resultado final</label>
              <input
                type="number"
                min={1}
                max={30}
                value={shortsTarget}
                onChange={(e) => setShortsTarget(Math.max(1, Math.min(30, Number(e.target.value) || 12)))}
              />
              <span className="form-hint">Faixa: 1 a 30 (padrão 12).</span>
            </div>
            <div className="form-group">
              <label>Qtd. longos no resultado final</label>
              <input
                type="number"
                min={1}
                max={10}
                value={longsTarget}
                onChange={(e) => setLongsTarget(Math.max(1, Math.min(10, Number(e.target.value) || 3)))}
              />
              <span className="form-hint">Faixa: 1 a 10 (padrão 3).</span>
            </div>
          </div>
          <button type="submit" disabled={creating || (!file && !sourceId && !youtubeUrl)}>
            {creating ? 'Iniciando...' : 'Gerar cortes'}
          </button>
        </form>
      </section>

      <section className="section">
        <div className="section-header-row">
          <h2>Fila de jobs</h2>
          <div className="job-actions-header">
            {analyses.some((a) => ['pending', 'transcribing', 'analyzing'].includes(a.status)) && (
              <button type="button" className="btn-reset-stuck" onClick={handleResetStuck} disabled={resettingStuck}>
                {resettingStuck ? 'Limpando...' : 'Limpar travados'}
              </button>
            )}
            {analyses.some((a) => ['pending', 'transcribing', 'analyzing', 'error'].includes(a.status)) && (
              <button type="button" className="btn-delete-stuck" onClick={handleDeleteStuck} disabled={deletingStuck}>
                {deletingStuck ? 'Deletando...' : 'Deletar travados/erros'}
              </button>
            )}
          </div>
        </div>
        {analyses.length === 0 ? (
          <p className="empty-msg">Nenhum job ainda.</p>
        ) : (
          <div className="analyses-list">
            {analyses.map((a) => (
              <div key={a.id} className="analysis-wrapper">
                <div
                  className={`analysis-card ${expandedId === a.id ? 'selected' : ''}`}
                  onClick={() => setExpandedId(expandedId === a.id ? null : a.id)}
                >
                  <div className="analysis-header">
                    <span className="analysis-name">{a.name || `Job #${a.id}`}</span>
                    <div className="analysis-header-right">
                      <span className="analysis-status" data-status={a.status}>
                        {statusLabel[a.status] || a.status}
                      </span>
                      <button
                        type="button"
                        className="btn-delete-job-card"
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteJob(a) }}
                        disabled={deletingJob === a.id}
                        title="Deletar job"
                      >
                        {deletingJob === a.id ? '...' : 'Deletar'}
                      </button>
                    </div>
                  </div>
                  {(a.status === 'transcribing' || a.status === 'analyzing') && (
                    <div className="analysis-progress">
                      <div className="progress-bar">
                        <div className="progress-fill" style={{ width: `${a.progress || 0}%` }} />
                      </div>
                      <span className="progress-msg">{a.progress_message}</span>
                    </div>
                  )}
                  {a.status === 'error' && a.error && <p className="analysis-error">{a.error}</p>}
                  {a.status === 'done' && a.cortes?.length > 0 && (
                    <span className="suggestion-count">{a.cortes.length} cortes</span>
                  )}
                </div>

                {expandedId === a.id && a.status === 'done' && a.cortes?.length > 0 && (
                  <div className="analysis-expanded">
                    <h3>Cortes curtos (até 3 min)</h3>
                    <div className="cortes-horizontal">
                      {shortCortes.map((c) => (
                        <CorteCard
                          key={c.id}
                          corte={c}
                          onChange={(f, v) => handleCorteChange(c, f, v)}
                          onPreview={() => c.file_url && window.open(c.file_url)}
                          onEditLegenda={() => setEditLegendaCorte(c)}
                        />
                      ))}
                    </div>
                    <h3>Cortes longos (acima de 3 min)</h3>
                    <div className="cortes-horizontal">
                      {longCortes.map((c) => (
                        <CorteCard
                          key={c.id}
                          corte={c}
                          onChange={(f, v) => handleCorteChange(c, f, v)}
                          onPreview={() => c.file_url && window.open(c.file_url)}
                          onEditLegenda={() => setEditLegendaCorte(c)}
                        />
                      ))}
                    </div>
                    <div className="vertical-reformat-block">
                      <h4>Reenquadramento vertical (cortes curtos 16:9 → 9:16)</h4>
                      <p className="form-hint">
                        Aplica aos cortes curtos quando o vídeo fonte for 16:9. O logo da marca (Mídias da marca) é usado automaticamente em &quot;Enquadrar e centralizar&quot;.
                      </p>
                      <div className="vertical-options-fields">
                        <div className="options-row">
                          <label>
                            <span>Modo</span>
                            <select
                              value={verticalOptions.mode}
                              onChange={(e) => setVerticalOptions((o) => ({ ...o, mode: e.target.value }))}
                            >
                              <option value="frame_center">Enquadrar e centralizar</option>
                              <option value="zoom_crop">Zoom e corte</option>
                            </select>
                          </label>
                          <label>
                            <span>Cor de fundo</span>
                            <div className="color-input-row">
                              <input
                                type="color"
                                value={verticalOptions.background_color}
                                onChange={(e) => setVerticalOptions((o) => ({ ...o, background_color: e.target.value }))}
                              />
                              <input
                                type="text"
                                value={verticalOptions.background_color}
                                onChange={(e) => setVerticalOptions((o) => ({ ...o, background_color: e.target.value }))}
                                className="color-hex"
                                placeholder="#000000"
                              />
                            </div>
                          </label>
                        </div>
                        {verticalOptions.mode === 'frame_center' && (
                          <>
                            <div className="options-row">
                              <label>
                                <span>Título: tamanho</span>
                                <input
                                  type="number"
                                  min={12}
                                  max={96}
                                  value={verticalOptions.font_size_title}
                                  onChange={(e) => setVerticalOptions((o) => ({ ...o, font_size_title: Number(e.target.value) || 36 }))}
                                />
                              </label>
                              <label>
                                <span>Título: cor</span>
                                <div className="color-input-row">
                                  <input
                                    type="color"
                                    value={verticalOptions.title_color}
                                    onChange={(e) => setVerticalOptions((o) => ({ ...o, title_color: e.target.value }))}
                                  />
                                  <input
                                    type="text"
                                    value={verticalOptions.title_color}
                                    onChange={(e) => setVerticalOptions((o) => ({ ...o, title_color: e.target.value }))}
                                    className="color-hex"
                                    placeholder="#FFFFFF"
                                  />
                                </div>
                              </label>
                            </div>
                            <div className="options-row">
                              <label className="flex-grow">
                                <span>Texto inferior</span>
                                <input
                                  type="text"
                                  value={verticalOptions.custom_text}
                                  onChange={(e) => setVerticalOptions((o) => ({ ...o, custom_text: e.target.value }))}
                                  placeholder="Ex: www.smclab.com.br"
                                />
                                <span className="form-hint">Suporta emojis. Aparece abaixo do título.</span>
                              </label>
                              <label>
                                <span>Tamanho</span>
                                <input
                                  type="number"
                                  min={12}
                                  max={72}
                                  value={verticalOptions.font_size_text}
                                  onChange={(e) => setVerticalOptions((o) => ({ ...o, font_size_text: Number(e.target.value) || 28 }))}
                                />
                              </label>
                              <label>
                                <span>Cor</span>
                                <div className="color-input-row">
                                  <input
                                    type="color"
                                    value={verticalOptions.text_color}
                                    onChange={(e) => setVerticalOptions((o) => ({ ...o, text_color: e.target.value }))}
                                  />
                                  <input
                                    type="text"
                                    value={verticalOptions.text_color}
                                    onChange={(e) => setVerticalOptions((o) => ({ ...o, text_color: e.target.value }))}
                                    className="color-hex"
                                    placeholder="#FFFFFF"
                                  />
                                </div>
                              </label>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="horizontal-reformat-block">
                      <h4>Cortes horizontais (longos)</h4>
                      <p className="form-hint">
                        Opções para cortes em formato 16:9 (vídeos longos).
                      </p>
                      <div className="horizontal-options-fields">
                        <label>
                          <span>Inserir logo</span>
                          <select
                            value={horizontalOptions.insert_logo ? 'yes' : 'no'}
                            onChange={(e) => setHorizontalOptions((o) => ({ ...o, insert_logo: e.target.value === 'yes' }))}
                          >
                            <option value="no">Não</option>
                            <option value="yes">Sim</option>
                          </select>
                        </label>
                        {horizontalOptions.insert_logo && (
                          <label>
                            <span>Posição do logo (X:Y em px)</span>
                            <div className="position-input-row">
                              <input
                                type="number"
                                min={0}
                                max={2000}
                                placeholder="X"
                                value={horizontalOptions.logo_x}
                                onChange={(e) => setHorizontalOptions((o) => ({ ...o, logo_x: Number(e.target.value) || 0 }))}
                              />
                              <span>:</span>
                              <input
                                type="number"
                                min={0}
                                max={1200}
                                placeholder="Y"
                                value={horizontalOptions.logo_y}
                                onChange={(e) => setHorizontalOptions((o) => ({ ...o, logo_y: Number(e.target.value) || 0 }))}
                              />
                            </div>
                            <span className="form-hint">X: 0–2000, Y: 0–1200. Ex: 100:100 = 100px do topo e 100px da esquerda.</span>
                          </label>
                        )}
                      </div>
                    </div>
                    <div className="overlay-animation-block">
                      <h4>Animação overlay (cortes curtos e longos)</h4>
                      <p className="form-hint">
                        PNG ou GIF com fundo transparente, sobreposto em um canto do vídeo. Para SVG, exporte como PNG. Cadastre em Mídias da marca → Animação overlay.
                      </p>
                      <div className="overlay-animation-fields">
                        <label>
                          <span>Animação</span>
                          <select
                            value={overlayAnimationOptions.asset_id}
                            onChange={(e) => setOverlayAnimationOptions((o) => ({ ...o, asset_id: e.target.value }))}
                          >
                            <option value="">Nenhuma</option>
                            {animationAssets.map((a) => (
                              <option key={a.id} value={a.id}>
                                {a.label || (typeof a.file === 'string' ? a.file.split('/').pop() : '') || `Animação #${a.id}`}
                              </option>
                            ))}
                          </select>
                        </label>
                        {overlayAnimationOptions.asset_id && (
                          <>
                            <label>
                              <span>Posição</span>
                              <select
                                value={overlayAnimationOptions.position}
                                onChange={(e) => setOverlayAnimationOptions((o) => ({ ...o, position: e.target.value }))}
                              >
                                <option value="top_left">Canto superior esquerdo</option>
                                <option value="top_right">Canto superior direito</option>
                                <option value="bottom_left">Canto inferior esquerdo</option>
                                <option value="bottom_right">Canto inferior direito</option>
                              </select>
                            </label>
                            <label>
                              <span>Margem (px)</span>
                              <input
                                type="number"
                                min={0}
                                max={100}
                                value={overlayAnimationOptions.margin}
                                onChange={(e) => setOverlayAnimationOptions((o) => ({ ...o, margin: Number(e.target.value) || 24 }))}
                              />
                            </label>
                            <label>
                              <span>Altura (px)</span>
                              <input
                                type="number"
                                min={20}
                                max={400}
                                value={overlayAnimationOptions.height}
                                onChange={(e) => setOverlayAnimationOptions((o) => ({ ...o, height: Number(e.target.value) || 120 }))}
                              />
                            </label>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="subtitle-style-block">
                      <h4>Estilo da legenda (vale para todos com legenda marcada)</h4>
                      <div className="subtitle-style-fields">
                        <label>
                          <span>Fonte</span>
                          <input
                            type="text"
                            value={subtitleStyle.font}
                            onChange={(e) => setSubtitleStyle((s) => ({ ...s, font: e.target.value }))}
                            placeholder="Helvetica"
                          />
                        </label>
                        <label>
                          <span>Tamanho</span>
                          <input
                            type="number"
                            min={12}
                            max={72}
                            value={subtitleStyle.size}
                            onChange={(e) => setSubtitleStyle((s) => ({ ...s, size: Number(e.target.value) || 24 }))}
                          />
                        </label>
                        <label>
                          <span>Cor da fonte</span>
                          <div className="color-input-row">
                            <input
                              type="color"
                              value={subtitleStyle.color}
                              onChange={(e) => setSubtitleStyle((s) => ({ ...s, color: e.target.value }))}
                            />
                            <input
                              type="text"
                              value={subtitleStyle.color}
                              onChange={(e) => setSubtitleStyle((s) => ({ ...s, color: e.target.value }))}
                              className="color-hex"
                              placeholder="#FFFFFF"
                            />
                          </div>
                        </label>
                        <label>
                          <span>Cor da borda</span>
                          <div className="color-input-row">
                            <input
                              type="color"
                              value={subtitleStyle.outline_color}
                              onChange={(e) => setSubtitleStyle((s) => ({ ...s, outline_color: e.target.value }))}
                            />
                            <input
                              type="text"
                              value={subtitleStyle.outline_color}
                              onChange={(e) => setSubtitleStyle((s) => ({ ...s, outline_color: e.target.value }))}
                              className="color-hex"
                              placeholder="#000000"
                            />
                          </div>
                        </label>
                      </div>
                    </div>
                    <div className="job-actions">
                      <button
                        type="button"
                        className="btn-finalizar"
                        onClick={handleFinalizar}
                        disabled={finalizing === a.id}
                      >
                        {finalizing === a.id ? 'Finalizando...' : 'Finalizar cortes'}
                      </button>
                      <button
                        type="button"
                        className="btn-delete-job"
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteJob(a) }}
                        disabled={deletingJob === a.id}
                      >
                        {deletingJob === a.id ? 'Deletando...' : 'Deletar job'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="section">
        <h2>Cortes finalizados</h2>
        <p className="section-desc">Cortes agrupados por job. Clique para expandir e ver a lista.</p>
        <div className="filters-row">
          <input
            type="date"
            placeholder="De"
            value={filters.date_from}
            onChange={(e) => setFilters((f) => ({ ...f, date_from: e.target.value }))}
          />
          <input
            type="date"
            placeholder="Até"
            value={filters.date_to}
            onChange={(e) => setFilters((f) => ({ ...f, date_to: e.target.value }))}
          />
          <select
            value={filters.format}
            onChange={(e) => setFilters((f) => ({ ...f, format: e.target.value }))}
          >
            <option value="">Todos os formatos</option>
            <option value="vertical">Vertical</option>
            <option value="horizontal">Horizontal</option>
          </select>
        </div>
        {finalizedCortes.length === 0 ? (
          <p className="empty-msg">Nenhum corte finalizado.</p>
        ) : (
          <div className="finalized-groups">
            {(() => {
              const byJob = {}
              finalizedCortes.forEach((c) => {
                const aid = c.analysis_id ?? c.analysis
                if (!byJob[aid]) byJob[aid] = { name: c.analysis_name || `Job #${aid}`, cortes: [] }
                byJob[aid].cortes.push(c)
              })
              return Object.entries(byJob).map(([aid, { name, cortes }]) => {
                const isExpanded = expandedFinalizedId === Number(aid)
                return (
                  <div key={aid} className="finalized-group">
                    <div
                      className={`finalized-group-header ${isExpanded ? 'expanded' : ''}`}
                      onClick={() => setExpandedFinalizedId(isExpanded ? null : Number(aid))}
                    >
                      <span className="finalized-group-chevron">{isExpanded ? '▼' : '▶'}</span>
                      <span className="finalized-group-name">{name}</span>
                      <span className="finalized-group-count">{cortes.length} corte{cortes.length !== 1 ? 's' : ''}</span>
                      <button
                        type="button"
                        className="btn-publish-all"
                        onClick={(e) => { e.stopPropagation(); handleOpenBulkPublish(Number(aid), name) }}
                        title="Distribuir e agendar todos os cortes do job na janela"
                      >
                        Publicar Tudo
                      </button>
                      <button
                        type="button"
                        className="btn-delete-all"
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteFinalizedJob({ id: Number(aid), name }) }}
                        disabled={deletingFinalizedJob === Number(aid)}
                        title="Deletar job e todos os cortes"
                      >
                        {deletingFinalizedJob === Number(aid) ? 'Deletando...' : 'Deletar tudo'}
                      </button>
                    </div>
                    {isExpanded && (
                      <div className="finalized-group-body">
                        <table className="finalized-table">
                          <thead>
                            <tr>
                              <th>Título</th>
                              <th>Data</th>
                              <th>Duração</th>
                              <th>Formato</th>
                              <th></th>
                            </tr>
                          </thead>
                          <tbody>
                            {cortes.map((c) => (
                              <tr key={c.id}>
                                <td>{c.suggestion?.title || '-'}</td>
                                <td>{c.created_at?.slice(0, 10)}</td>
                                <td>
                                  {c.suggestion?.duration_seconds != null
                                    ? formatDuration(c.suggestion.duration_seconds)
                                    : c.suggestion?.duration_minutes != null
                                      ? formatDurationMin(c.suggestion.duration_minutes)
                                      : '-'}
                                </td>
                                <td>{c.format === 'vertical' ? 'Vertical' : 'Horizontal'}</td>
                                <td>
                                  <button
                                    type="button"
                                    className={`btn-thumb ${c.thumbnail_url ? 'has-thumb' : ''}`}
                                    onClick={() => setThumbnailModal(c)}
                                    title="Visualizar capa"
                                    disabled={uploadingThumbnailId === c.id}
                                  >
                                    {uploadingThumbnailId === c.id ? 'Enviando...' : c.thumbnail_url ? 'Visualizar capa' : 'Visualizar capa'}
                                  </button>
                                  <button type="button" className="btn-view" onClick={() => c.file_url && window.open(c.file_url)}>
                                    Visualizar
                                  </button>
                                  <button type="button" className="btn-schedule-one" onClick={() => handleOpenUnitSchedule(c)}>
                                    Agendar
                                  </button>
                                  <button type="button" className="btn-delete" onClick={() => handleDeleteCorte(c)}>
                                    Deletar
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )
              })
            })()}
          </div>
        )}
      </section>

      {confirmDeleteJob && (
        <div className="modal-overlay" onClick={() => setConfirmDeleteJob(null)}>
          <div className="modal confirm-delete-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Deletar job</h3>
            <p>Deseja mesmo deletar &quot;{confirmDeleteJob.name || `Job #${confirmDeleteJob.id}`}&quot;? Esta ação não pode ser desfeita.</p>
            <div className="modal-actions">
              <button type="button" onClick={() => setConfirmDeleteJob(null)}>Cancelar</button>
              <button
                type="button"
                className="btn-confirm-delete"
                onClick={() => handleDeleteJob(confirmDeleteJob)}
                disabled={deletingJob === confirmDeleteJob.id}
              >
                {deletingJob === confirmDeleteJob.id ? 'Deletando...' : 'Deletar'}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDeleteFinalizedJob && (
        <div className="modal-overlay" onClick={() => setConfirmDeleteFinalizedJob(null)}>
          <div className="modal confirm-delete-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Deletar job finalizado</h3>
            <p>Deseja mesmo deletar &quot;{confirmDeleteFinalizedJob.name}&quot; e todos os seus cortes? Esta ação não pode ser desfeita.</p>
            <div className="modal-actions">
              <button type="button" onClick={() => setConfirmDeleteFinalizedJob(null)}>Cancelar</button>
              <button
                type="button"
                className="btn-confirm-delete"
                onClick={() => handleDeleteFinalizedJob(confirmDeleteFinalizedJob.id)}
                disabled={deletingFinalizedJob === confirmDeleteFinalizedJob.id}
              >
                {deletingFinalizedJob === confirmDeleteFinalizedJob.id ? 'Deletando...' : 'Deletar tudo'}
              </button>
            </div>
          </div>
        </div>
      )}

      {bulkPublishModal && (
        <div className="modal-overlay" onClick={() => !bulkPublishing && setBulkPublishModal(null)}>
          <div className="modal confirm-delete-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Publicar Tudo</h3>
            <p>
              Defina a janela para distribuir os cortes do job <strong>{bulkPublishModal.name}</strong>.
              Curtos serão agendados em YT e longos em YTB.
            </p>
            <form className="bulk-publish-form" onSubmit={handleBulkPublishSubmit}>
              <label>
                Início
                <input
                  type="datetime-local"
                  value={bulkPublishModal.startAt}
                  onChange={(e) => setBulkPublishModal((m) => ({ ...m, startAt: e.target.value }))}
                  required
                />
              </label>
              <label>
                Fim
                <input
                  type="datetime-local"
                  value={bulkPublishModal.endAt}
                  onChange={(e) => setBulkPublishModal((m) => ({ ...m, endAt: e.target.value }))}
                  required
                />
              </label>
              <label>
                Visibilidade
                <select
                  value={bulkPublishModal.privacyStatus}
                  onChange={(e) => setBulkPublishModal((m) => ({ ...m, privacyStatus: e.target.value }))}
                >
                  <option value="private">Privado</option>
                  <option value="unlisted">Não listado</option>
                  <option value="public">Público</option>
                </select>
              </label>
              {socialAccounts.some((a) => a.platform === 'YTB' || a.platform === 'YT') && (
                <label>
                  Canal YouTube
                  <select
                    value={bulkPublishModal.socialAccountId || ''}
                    onChange={(e) => setBulkPublishModal((m) => ({ ...m, socialAccountId: e.target.value }))}
                  >
                    <option value="">Usar primeira conta da marca</option>
                    {socialAccounts
                      .filter((a) => a.platform === 'YTB' || a.platform === 'YT')
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.account_name || a.channel_id || `Canal ${a.id}`}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <label>
                Preview (massa)
                <textarea
                  value={bulkPublishModal.descriptionPreview || ''}
                  readOnly
                  rows={10}
                />
              </label>
              <div className="modal-actions">
                <button type="button" onClick={() => setBulkPublishModal(null)} disabled={bulkPublishing}>Cancelar</button>
                <button type="submit" className="btn-confirm-delete" disabled={bulkPublishing}>
                  {bulkPublishing ? 'Agendando...' : 'Agendar em Massa'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {unitScheduleModal && (
        <div className="modal-overlay" onClick={() => !unitScheduling && setUnitScheduleModal(null)}>
          <div className="modal confirm-delete-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Agendar corte</h3>
            <p>Defina data e hora para agendar <strong>{unitScheduleModal.title}</strong>.</p>
            <form className="bulk-publish-form" onSubmit={handleUnitScheduleSubmit}>
              <label>
                Data e hora
                <input
                  type="datetime-local"
                  value={unitScheduleModal.scheduledAt}
                  onChange={(e) => setUnitScheduleModal((m) => ({ ...m, scheduledAt: e.target.value }))}
                  required
                />
              </label>
              <label>
                Visibilidade
                <select
                  value={unitScheduleModal.privacyStatus}
                  onChange={(e) => setUnitScheduleModal((m) => ({ ...m, privacyStatus: e.target.value }))}
                >
                  <option value="private">Privado</option>
                  <option value="unlisted">Não listado</option>
                  <option value="public">Público</option>
                </select>
              </label>
              <label>
                Preview da descrição final
                <textarea
                  value={unitScheduleModal.descriptionPreview || ''}
                  readOnly
                  rows={10}
                />
              </label>
              <div className="modal-actions">
                <button type="button" onClick={() => setUnitScheduleModal(null)} disabled={unitScheduling}>Cancelar</button>
                <button type="submit" className="btn-confirm-delete" disabled={unitScheduling}>
                  {unitScheduling ? 'Agendando...' : 'Agendar'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {thumbnailModal && (
        <ThumbnailModal
          corte={thumbnailModal}
          uploading={uploadingThumbnailId === thumbnailModal.id}
          onClose={() => setThumbnailModal(null)}
          onReplace={(file) => handleReplaceThumbnail(thumbnailModal, file)}
        />
      )}

      {editLegendaCorte && (
        <LegendaEditModal
          corte={editLegendaCorte}
          onClose={() => setEditLegendaCorte(null)}
          onSave={(segments) => handleSaveLegenda(editLegendaCorte, segments)}
        />
      )}
    </div>
  )
}

function LegendaEditModal({ corte, onClose, onSave }) {
  const [segments, setSegments] = useState(corte.subtitle_segments || [])
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setSegments(corte.subtitle_segments || [])
  }, [corte.id, corte.subtitle_segments])

  function secToTc(sec) {
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    const s = Math.floor(sec % 60)
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  function updateSegment(i, text) {
    setSegments((prev) => prev.map((s, idx) => (idx === i ? { ...s, text } : s)))
  }

  async function handleOk() {
    setSaving(true)
    try {
      await onSave(segments)
      onClose()
    } finally {
      setSaving(false)
    }
  }

  const title = corte.suggestion?.title || `Corte #${corte.id}`

  return (
    <div className="modal-overlay legenda-modal-overlay" onClick={onClose}>
      <div className="legenda-modal" onClick={(e) => e.stopPropagation()}>
        <div className="legenda-modal-header">
          <h3>Editar legenda – {title}</h3>
          <button type="button" className="legenda-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="legenda-modal-body">
          {segments.length === 0 ? (
            <p className="legenda-empty">Este corte não possui legendas.</p>
          ) : (
            <div className="legenda-segments-list">
              {segments.map((seg, i) => (
                <div key={i} className="legenda-segment">
                  <span className="legenda-segment-time">{secToTc(seg.start)} → {secToTc(seg.end)}</span>
                  <textarea
                    value={seg.text}
                    onChange={(e) => updateSegment(i, e.target.value)}
                    rows={2}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="legenda-modal-footer">
          <button type="button" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn-ok-legenda" onClick={handleOk} disabled={saving || segments.length === 0}>
            {saving ? 'Salvando...' : 'OK'}
          </button>
        </div>
      </div>
    </div>
  )
}

function CorteCard({ corte, onChange, onPreview, onEditLegenda }) {
  const [localTitle, setLocalTitle] = useState(corte.suggestion?.title || '')
  const s = corte.suggestion || {}
  useEffect(() => {
    setLocalTitle(s.title || '')
  }, [s.title])
  return (
    <div className="corte-card">
      <div className="corte-preview" onClick={onPreview}>
        {corte.file_url ? (
          <span className="corte-preview-btn">▶ Preview</span>
        ) : (
          <span className="corte-preview-placeholder">...</span>
        )}
      </div>
      <div className="corte-info">
        <label className="corte-title-label">
          <span className="corte-title-hint">Título (edite para evitar ban)</span>
          <input
            type="text"
            className="corte-title-input"
            value={localTitle}
            onChange={(e) => setLocalTitle(e.target.value)}
            onBlur={() => {
              const v = localTitle.trim()
              if (v !== (s.title || '')) onChange('title', v)
            }}
            placeholder="Sem título"
            maxLength={200}
          />
        </label>
        {s.virality_score != null && <span className="corte-score">Score: {s.virality_score}/100</span>}
        <span className="corte-time">
          {s.start_tc} → {s.end_tc}
        </span>
      </div>
      <div className="corte-checkboxes">
        <label>
          <input
            type="checkbox"
            checked={corte.needs_subtitle}
            onChange={(e) => onChange('needs_subtitle', e.target.checked)}
          />
          Legenda
        </label>
        <button
          type="button"
          className="btn-edit-legenda"
          onClick={(e) => { e.stopPropagation(); onEditLegenda?.() }}
          title="Editar texto da legenda"
        >
          Editar legenda
        </button>
        <label>
          <input
            type="checkbox"
            checked={corte.user_wants_finalize}
            onChange={(e) => onChange('user_wants_finalize', e.target.checked)}
          />
          Finalizar
        </label>
      </div>
    </div>
  )
}

function ThumbnailModal({ corte, uploading, onClose, onReplace }) {
  function handleSelectFile() {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/png,image/jpeg,image/jpg,image/gif'
    input.onchange = (e) => {
      const file = e.target.files?.[0]
      if (file) onReplace(file)
    }
    input.click()
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal thumb-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Capa do vídeo</h3>
        {corte.thumbnail_url ? (
          <img className="thumb-modal-image" src={corte.thumbnail_url} alt="Prévia da capa" />
        ) : (
          <div className="thumb-modal-empty">
            Capa automática ainda não disponível para este corte.
          </div>
        )}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>Fechar</button>
          <button
            type="button"
            className="btn-confirm-delete"
            onClick={handleSelectFile}
            disabled={uploading}
          >
            {uploading ? 'Substituindo...' : 'Substituir capa'}
          </button>
        </div>
      </div>
    </div>
  )
}
