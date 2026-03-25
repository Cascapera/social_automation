import { useState, useEffect, useRef } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getAutoCutAnalyses,
  getAutoCutAnalysis,
  createAutoCutAnalysis,
  createReadyCutsAnalysis,
  getFactory,
  updateFactory,
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
  updateBrand,
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
  const { brandId, brands, viewMode, factoryId, setBrands } = useBrand()
  const [analyses, setAnalyses] = useState([])
  const [factoryRunningAnalyses, setFactoryRunningAnalyses] = useState([])
  const [sources, setSources] = useState([])
  const [socialAccounts, setSocialAccounts] = useState([])
  const [expandedId, setExpandedId] = useState(null)
  const [file, setFile] = useState(null)
  const [sourceId, setSourceId] = useState('')
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [name, setName] = useState('')
  const [assunto, setAssunto] = useState('')
  const [convidados, setConvidados] = useState('')
  const [targetBrandId, setTargetBrandId] = useState('')
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
    size: 12,
    color: '#FFFFFF',
    outline_color: '#000000',
  })
  const [verticalOptions, setVerticalOptions] = useState({
    mode: 'zoom_crop',
    background_color: '#000000',
    custom_text: '',
    font_size_title: 36,
    font_size_text: 28,
    title_color: '#FFFFFF',
    text_color: '#FFFFFF',
  })
  const [horizontalOptions, setHorizontalOptions] = useState({
    logo_x: 100,
    logo_y: 100,
  })
  const [longVideoSubtitles, setLongVideoSubtitles] = useState(false)
  const [longVideoLogo, setLongVideoLogo] = useState(false)
  const [readyCutsLongSubs, setReadyCutsLongSubs] = useState(false)
  const [readyCutsLongLogo, setReadyCutsLongLogo] = useState(false)
  const [animationAssets, setAnimationAssets] = useState([])
  const [longOverlayAssets, setLongOverlayAssets] = useState([])
  const [longOverlayEnabled, setLongOverlayEnabled] = useState(false)
  const [longOverlayAssetId, setLongOverlayAssetId] = useState('')
  const [readyCutsLongOverlayEnabled, setReadyCutsLongOverlayEnabled] = useState(false)
  const [readyCutsLongOverlayAssetId, setReadyCutsLongOverlayAssetId] = useState('')
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
  const [titleEditCorteId, setTitleEditCorteId] = useState(null)
  const [titleEditValue, setTitleEditValue] = useState('')
  const [savingTitleId, setSavingTitleId] = useState(null)
  const [factoryInfo, setFactoryInfo] = useState(null)
  const [togglingFactoryProcessing, setTogglingFactoryProcessing] = useState(false)
  const [readyCutsBrandId, setReadyCutsBrandId] = useState('')
  const [creatingReadyCuts, setCreatingReadyCuts] = useState(false)
  const [jobVerticalMode, setJobVerticalMode] = useState('zoom_crop')
  const readyCutsFileInputRef = useRef(null)
  const [readyCutsModalOpen, setReadyCutsModalOpen] = useState(false)
  const [readyCutsModalFiles, setReadyCutsModalFiles] = useState([])
  const [readyCutsJobName, setReadyCutsJobName] = useState('')
  const [readyCutsTranscribe, setReadyCutsTranscribe] = useState(true)
  const [readyCutsLongVideo, setReadyCutsLongVideo] = useState(false)
  const [readyCutsTitlesLanguage, setReadyCutsTitlesLanguage] = useState('pt')

  const selectedAnalysis = analyses.find((a) => a.id === expandedId)
  const factoryBrands = viewMode === 'factory' && factoryId
    ? (brands || []).filter((b) => String(b.factory || b.factory_id || '') === String(factoryId))
    : []
  const factoryBrandIds = factoryBrands.map((b) => b?.id).filter(Boolean)
  const factoryBrandIdsKey = factoryBrandIds.length ? [...factoryBrandIds].sort().join(',') : ''
  const fallbackFactoryBrandId = viewMode === 'factory'
    ? (factoryBrands[0]?.id || brands[0]?.id || null)
    : null
  const activeBrandId = brandId || fallbackFactoryBrandId
  const selectedBrand = brands.find((b) => String(b.id) === String(activeBrandId))
  const hasRunningAnalyses = analyses.some((a) => ['pending', 'transcribing', 'analyzing'].includes(a.status))
    || factoryRunningAnalyses.some((a) => ['pending', 'transcribing', 'analyzing'].includes(a.status))
  const displayAnalyses = (() => {
    const byId = new Map()
    ;[...factoryRunningAnalyses, ...analyses].forEach((a) => {
      byId.set(a.id, a)
    })
    return Array.from(byId.values()).sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
  })()

  function buildDescriptionBody(analysis, brandExtra = '') {
    if (!analysis) return ''
    const videoName = (analysis.name || '').trim() || 'Vídeo original'
    const convidados = (analysis.convidados || '').trim() || '-'
    const pv = (analysis.prompt_version || '').trim().toLowerCase()
    const isEn = pv.endsWith('_en')
    const lines = isEn
      ? [`🎙️ Clip from live: ${videoName}`, '', `Guest: ${convidados}`]
      : [`🎙️ Corte da live: ${videoName}`, '', `Convidado: ${convidados}`]
    const youtubeUrl = (analysis.youtube_url || '').trim()
    if (youtubeUrl) {
      lines.push('', isEn ? '📺 Full episode:' : '📺 Episódio completo:', youtubeUrl)
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

  async function loadFactoryRunningAnalyses() {
    if (viewMode !== 'factory') {
      setFactoryRunningAnalyses([])
      return
    }
    // Fila universal: analyses já tem todos os jobs. Não precisa fetch extra.
    setFactoryRunningAnalyses([])
  }

  async function loadAnalysesForView() {
    if (viewMode === 'factory') {
      // Fila de jobs: universal (todas as factories). Sem filtro de brand.
      const list = await getAutoCutAnalyses(null, { excludeFinalized: true })
      const dedup = (Array.isArray(list) ? list : [])
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
      setAnalyses(dedup)
      return dedup
    }
    if (!activeBrandId) {
      setAnalyses([])
      return []
    }
    if (viewMode !== 'factory') {
      const list = await getAutoCutAnalyses(activeBrandId, { excludeFinalized: true })
      setAnalyses(list)
      return list
    }
    return []
  }

  async function loadFinalizedCortes() {
    if (viewMode === 'factory') {
      if (factoryBrandIds.length === 0) {
        setFinalizedCortes([])
        return
      }
      try {
        const settled = await Promise.allSettled(
          factoryBrandIds.map((id) => getAutoCutCortes(id, { finalized: true, ...filters })),
        )
        const rows = settled
          .filter((r) => r.status === 'fulfilled')
          .map((r) => r.value)
        const merged = rows.flat()
        const dedup = Array.from(new Map(merged.map((c) => [c.id, c])).values())
        setFinalizedCortes(dedup)
      } catch {
        setFinalizedCortes([])
      }
      return
    }
    if (!activeBrandId) {
      setFinalizedCortes([])
      return
    }
    getAutoCutCortes(activeBrandId, { finalized: true, ...filters }).then(setFinalizedCortes).catch(() => setFinalizedCortes([]))
  }

  async function loadFactoryInfo() {
    if (viewMode !== 'factory' || !factoryId) {
      setFactoryInfo(null)
      return
    }
    try {
      const info = await getFactory(factoryId)
      setFactoryInfo(info)
    } catch {
      setFactoryInfo(null)
    }
  }

  async function persistLongVideoPreference(field, value) {
    if (!activeBrandId) return
    setError('')
    try {
      const updated = await updateBrand(activeBrandId, { [field]: value })
      setBrands((prev) =>
        prev.map((b) => (String(b.id) === String(activeBrandId) ? { ...b, ...updated } : b)),
      )
    } catch (e) {
      setError(e.message)
    }
  }

  async function persistReadyCutsLongVideo(field, value) {
    const bid = viewMode === 'factory' ? readyCutsBrandId : activeBrandId
    if (!bid) return
    try {
      const updated = await updateBrand(bid, { [field]: value })
      setBrands((prev) =>
        prev.map((b) => (String(b.id) === String(bid) ? { ...b, ...updated } : b)),
      )
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleToggleFactoryProcessing() {
    if (!factoryInfo?.id || togglingFactoryProcessing) return
    setError('')
    setTogglingFactoryProcessing(true)
    try {
      const updated = await updateFactory(factoryInfo.id, {
        processing_paused: !factoryInfo.processing_paused,
      })
      setFactoryInfo(updated)
    } catch (e) {
      setError(e.message)
    } finally {
      setTogglingFactoryProcessing(false)
    }
  }

  useEffect(() => {
    loadFactoryInfo()
  }, [viewMode, factoryId])

  useEffect(() => {
    if (viewMode === 'factory' && factoryId && targetBrandId && targetBrandId !== 'distribute' && factoryBrandIds.length > 0) {
      const isValid = factoryBrandIds.some((id) => String(id) === String(targetBrandId))
      if (!isValid) setTargetBrandId('')
    }
  }, [viewMode, factoryId, targetBrandId, factoryBrandIdsKey])

  useEffect(() => {
    const mode = selectedBrand?.vertical_mode || 'zoom_crop'
    setVerticalOptions((o) => (o.mode === mode ? o : { ...o, mode }))
  }, [activeBrandId, selectedBrand?.vertical_mode])

  useEffect(() => {
    const jobBrandId = selectedAnalysis?.brand
    if (jobBrandId != null && jobBrandId !== '') {
      const b = brands.find((x) => String(x.id) === String(jobBrandId))
      if (b) {
        setLongVideoSubtitles(!!b.long_video_subtitles_enabled)
        setLongVideoLogo(!!b.long_video_logo_enabled)
      }
      return
    }
    if (selectedBrand) {
      setLongVideoSubtitles(!!selectedBrand.long_video_subtitles_enabled)
      setLongVideoLogo(!!selectedBrand.long_video_logo_enabled)
    }
  }, [
    expandedId,
    selectedAnalysis?.brand,
    selectedBrand?.id,
    selectedBrand?.long_video_subtitles_enabled,
    selectedBrand?.long_video_logo_enabled,
    brands,
  ])

  useEffect(() => {
    if (!readyCutsModalOpen) return
    const bid = viewMode === 'factory' ? readyCutsBrandId : activeBrandId
    if (!bid) return
    const b = brands.find((x) => String(x.id) === String(bid))
    if (b) {
      setReadyCutsLongSubs(!!b.long_video_subtitles_enabled)
      setReadyCutsLongLogo(!!b.long_video_logo_enabled)
    }
  }, [readyCutsModalOpen, readyCutsBrandId, activeBrandId, viewMode, brands])

  useEffect(() => {
    const shouldLoadFactoryAggregated = viewMode === 'factory'
    if (shouldLoadFactoryAggregated || activeBrandId) {
      loadAnalysesForView().catch(() => setAnalyses([]))
      if (activeBrandId) {
        getSources(activeBrandId).then(setSources).catch(() => setSources([]))
        getBrandSocialAccounts(activeBrandId).then(setSocialAccounts).catch(() => setSocialAccounts([]))
        getBrandAssets(activeBrandId, 'ANIMATION').then(setAnimationAssets).catch(() => setAnimationAssets([]))
      } else {
        setSources([])
        setSocialAccounts([])
        setAnimationAssets([])
      }
      loadFinalizedCortes()
      loadFactoryRunningAnalyses()
    } else {
      setAnalyses([])
      setSocialAccounts([])
      setFinalizedCortes([])
      setFactoryRunningAnalyses([])
    }
  }, [activeBrandId, filters, viewMode, brandId, factoryId, factoryBrandIdsKey])

  useEffect(() => {
    const bid =
      readyCutsModalOpen && viewMode === 'factory' && readyCutsBrandId
        ? readyCutsBrandId
        : activeBrandId
    if (!bid) {
      setLongOverlayAssets([])
      return
    }
    getBrandAssets(bid, 'OVERLAY_LONG').then(setLongOverlayAssets).catch(() => setLongOverlayAssets([]))
  }, [activeBrandId, readyCutsModalOpen, readyCutsBrandId, viewMode])

  async function handleGenerate(e) {
    e.preventDefault()
    if (!activeBrandId) {
      setError(
        viewMode === 'factory'
          ? 'Cadastre ao menos uma Brand nesta Factory para iniciar os cortes.'
          : 'Selecione uma marca no menu à esquerda',
      )
      return
    }
    if (!file && !sourceId && !youtubeUrl) {
      setError('Envie um vídeo, selecione um source ou informe um URL do YouTube')
      return
    }
    setError('')
    if (longOverlayEnabled && !longOverlayAssetId) {
      setError('Selecione um overlay ou desative a opção.')
      return
    }
    setCreating(true)
    try {
      const useBrandThumbDefaults = viewMode === 'factory' && !!selectedBrand
      const effectiveThumbnailFont = useBrandThumbDefaults
        ? (selectedBrand.thumbnail_font || 'impact')
        : (thumbnailFont || 'impact')
      const effectiveThumbnailBandColor = useBrandThumbDefaults
        ? (selectedBrand.thumbnail_band_color || '#E12E20')
        : (thumbnailBandColor || '#E12E20')
      const effectiveThumbnailTextColor = useBrandThumbDefaults
        ? (selectedBrand.thumbnail_text_color || '#0A0A0A')
        : (thumbnailTextColor || '#0A0A0A')
      const effectiveThumbnailStrokeColor = useBrandThumbDefaults
        ? (selectedBrand.thumbnail_effect_color || '#FFEBDC')
        : (thumbnailStrokeColor || '#FFEBDC')

      const isDistribute = targetBrandId === 'distribute'
      const effectiveTargetBrandId = (targetBrandId && targetBrandId !== 'distribute') ? targetBrandId : undefined
      const distributionMode = isDistribute ? 'distribute' : 'theme'
      const a = await createAutoCutAnalysis({
        file: file || undefined,
        sourceId: sourceId || undefined,
        youtubeUrl: youtubeUrl || undefined,
        brandId: activeBrandId,
        targetBrandId: effectiveTargetBrandId,
        distributionMode,
        name: name || undefined,
        assunto: assunto || undefined,
        convidados: convidados || undefined,
        promptVersion: promptVersion || undefined,
        thumbnailFont: effectiveThumbnailFont || undefined,
        thumbnailBandColor: effectiveThumbnailBandColor || undefined,
        thumbnailTextColor: effectiveThumbnailTextColor || undefined,
        thumbnailStrokeColor: effectiveThumbnailStrokeColor || undefined,
        shortsTarget,
        longsTarget,
        verticalMode: jobVerticalMode,
        longOverlayEnabled,
        longOverlayAssetId: longOverlayEnabled && longOverlayAssetId ? Number(longOverlayAssetId) : null,
      })
      setAnalyses((prev) => [a, ...prev])
      setExpandedId(a.id)
      setFile(null)
      setSourceId('')
      setYoutubeUrl('')
      setName('')
      setAssunto('')
      setConvidados('')
      setTargetBrandId('')
      setPromptVersion('educational')
      setThumbnailFont('impact')
      setThumbnailBandColor('#E12E20')
      setThumbnailTextColor('#0A0A0A')
      setThumbnailStrokeColor('#FFEBDC')
      setShortsTarget(12)
      setLongsTarget(3)
      setLongOverlayEnabled(false)
      setLongOverlayAssetId('')
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  function handleReadyCutsFilePick(e) {
    const list = Array.from(e.target.files || [])
    e.target.value = ''
    if (!list.length) return
    setReadyCutsModalFiles(list)
    setReadyCutsModalOpen(true)
  }

  function moveReadyCutModal(index, delta) {
    setReadyCutsModalFiles((prev) => {
      const j = index + delta
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]
      ;[next[index], next[j]] = [next[j], next[index]]
      return next
    })
  }

  async function handleConfirmReadyCutsModal(e) {
    e.preventDefault()
    const effectiveBrandId = viewMode === 'factory' ? readyCutsBrandId : activeBrandId
    if (!effectiveBrandId) {
      setError(viewMode === 'factory' ? 'Selecione uma Brand da factory.' : 'Selecione uma marca no menu à esquerda.')
      return
    }
    const name = readyCutsJobName.trim()
    if (!name) {
      setError('Informe o nome do job.')
      return
    }
    if (!readyCutsModalFiles?.length) {
      setError('Selecione pelo menos um arquivo de vídeo.')
      return
    }
    if (readyCutsLongOverlayEnabled && !readyCutsLongOverlayAssetId) {
      setError('Selecione um overlay ou desative a opção.')
      return
    }
    setError('')
    setCreatingReadyCuts(true)
    try {
      const a = await createReadyCutsAnalysis({
        files: readyCutsModalFiles,
        brandId: effectiveBrandId,
        name,
        verticalMode: jobVerticalMode,
        transcribe: readyCutsTranscribe,
        createLongVideo: readyCutsLongVideo,
        titlesLanguage: readyCutsTitlesLanguage,
        longOverlayEnabled: readyCutsLongOverlayEnabled,
        longOverlayAssetId:
          readyCutsLongOverlayEnabled && readyCutsLongOverlayAssetId
            ? Number(readyCutsLongOverlayAssetId)
            : null,
      })
      setAnalyses((prev) => [a, ...prev])
      if (a?.id) setExpandedId(a.id)
      setReadyCutsModalOpen(false)
      setReadyCutsModalFiles([])
      setReadyCutsJobName('')
      setReadyCutsTranscribe(true)
      setReadyCutsLongVideo(false)
      setReadyCutsTitlesLanguage('pt')
      setReadyCutsLongOverlayEnabled(false)
      setReadyCutsLongOverlayAssetId('')
      loadAnalysesForView()
    } catch (err) {
      setError(err.message)
    } finally {
      setCreatingReadyCuts(false)
    }
  }

  async function handleResetStuck() {
    if (!activeBrandId) return
    setResettingStuck(true)
    setError('')
    try {
      const res = await resetStuckAutoCuts(activeBrandId)
      if (res.reset > 0) {
        const list = await loadAnalysesForView()
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
    if (!activeBrandId) return
    setDeletingStuck(true)
    setError('')
    try {
      const res = await deleteStuckAutoCuts(activeBrandId)
      if (res.deleted > 0) {
        const list = await loadAnalysesForView()
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
    const finalizeBrandId = selectedAnalysis.brand ?? activeBrandId
    if (!finalizeBrandId) {
      setError('Não foi possível identificar a marca deste job.')
      return
    }
    setFinalizing(selectedAnalysis.id)
    setError('')
    try {
      await updateBrand(finalizeBrandId, {
        long_video_subtitles_enabled: longVideoSubtitles,
        long_video_logo_enabled: longVideoLogo,
      })
      setBrands((prev) =>
        prev.map((b) =>
          String(b.id) === String(finalizeBrandId)
            ? {
                ...b,
                long_video_subtitles_enabled: longVideoSubtitles,
                long_video_logo_enabled: longVideoLogo,
              }
            : b,
        ),
      )
      await finalizarAutoCutJob(selectedAnalysis.id, {
        subtitle_style: subtitleStyle,
        vertical_mode: verticalOptions.mode,
        background_color: verticalOptions.background_color,
        custom_text: verticalOptions.custom_text,
        font_size_title: verticalOptions.font_size_title,
        font_size_text: verticalOptions.font_size_text,
        title_color: verticalOptions.title_color,
        text_color: verticalOptions.text_color,
        horizontal_insert_logo: longVideoLogo,
        horizontal_logo_x: longVideoLogo ? horizontalOptions.logo_x : undefined,
        horizontal_logo_y: longVideoLogo ? horizontalOptions.logo_y : undefined,
        overlay_animation_asset_id: overlayAnimationOptions.asset_id ? Number(overlayAnimationOptions.asset_id) : undefined,
        overlay_position: overlayAnimationOptions.asset_id ? overlayAnimationOptions.position : undefined,
        overlay_margin: overlayAnimationOptions.asset_id ? overlayAnimationOptions.margin : undefined,
        overlay_height: overlayAnimationOptions.asset_id ? overlayAnimationOptions.height : undefined,
        long_overlay_enabled: !!selectedAnalysis.long_overlay_enabled,
        long_overlay_asset_id: selectedAnalysis.long_overlay_asset ?? undefined,
      })
      const list = await loadAnalysesForView()
      setAnalyses(list)
      setExpandedId(null)
      await loadFinalizedCortes()
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
      await deleteAutoCutCorte(corte.id, null)
      await loadFinalizedCortes()
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleSaveFinalizedTitle(corte) {
    const t = titleEditValue.trim()
    if (!t) {
      setError('Informe um título.')
      return
    }
    setSavingTitleId(corte.id)
    setError('')
    try {
      await updateAutoCutCorte(corte.id, { title: t })
      setTitleEditCorteId(null)
      await loadFinalizedCortes()
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingTitleId(null)
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
      let refreshed = []
      if (viewMode === 'factory' && factoryBrandIds.length > 0) {
        const rows = await Promise.all(
          factoryBrandIds.map((id) => getAutoCutCortes(id, { finalized: true, ...filters })),
        )
        refreshed = Array.from(new Map(rows.flat().map((c) => [c.id, c])).values())
      } else {
        refreshed = await getAutoCutCortes(activeBrandId, { finalized: true, ...filters })
      }
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

      {viewMode === 'factory' && factoryInfo && (
        <section className="section factory-processing-control">
          <div className="factory-control-info">
            <strong>Factory: {factoryInfo.name}</strong>
            <span className={`factory-processing-badge ${factoryInfo.processing_paused ? 'paused' : 'running'}`}>
              {factoryInfo.processing_paused ? 'Fila de jobs pausada' : 'Fila de jobs ativa'}
            </span>
            {factoryInfo.processing_paused && (
              <div className="factory-paused-warning">
                Novos jobs não iniciam enquanto a pausa estiver ativa. O job já em execução continua até o fim.
              </div>
            )}
          </div>
          <button
            type="button"
            className={`factory-toggle-btn ${factoryInfo.processing_paused ? 'resume' : 'pause'}`}
            onClick={handleToggleFactoryProcessing}
            disabled={togglingFactoryProcessing}
          >
            {togglingFactoryProcessing
              ? 'Salvando...'
              : factoryInfo.processing_paused
                ? 'Continuar fila de jobs'
                : 'Pausar fila de jobs'}
          </button>
        </section>
      )}

      {((viewMode === 'factory' && factoryId) || (viewMode === 'brand' && brandId && !selectedBrand?.factory)) && (
        <section className="section">
          <h2>Upload cortes prontos</h2>
          <p className="form-hint" style={{ marginBottom: 12 }}>
            Selecione uma pasta com vários vídeos. Confirme a ordem no modal, preencha o nome do job e as opções, depois envie.
          </p>
          <input
            ref={readyCutsFileInputRef}
            type="file"
            accept="video/*"
            multiple
            style={{ display: 'none' }}
            onChange={handleReadyCutsFilePick}
          />
          <div className="auto-cut-form">
            <div className="form-group">
              <button
                type="button"
                className="ready-cuts-pick-btn"
                onClick={() => readyCutsFileInputRef.current?.click()}
              >
                Selecionar vídeos…
              </button>
            </div>
            {viewMode === 'factory' && factoryId && (
              <div className="form-group">
                <label>Brand de destino</label>
                <select
                  value={readyCutsBrandId}
                  onChange={(e) => setReadyCutsBrandId(e.target.value)}
                >
                  <option value="">Selecione uma brand</option>
                  {factoryBrands.map((b) => (
                    <option key={b.id} value={b.id}>{b.name || `Brand #${b.id}`}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {readyCutsModalOpen && (
            <div className="modal-overlay ready-cuts-modal-overlay" onClick={() => !creatingReadyCuts && setReadyCutsModalOpen(false)}>
              <div className="ready-cuts-order-modal" onClick={(e) => e.stopPropagation()}>
                <div className="ready-cuts-order-modal-header">
                  <h3>Ordem dos vídeos e opções do job</h3>
                  <button type="button" className="legenda-modal-close" disabled={creatingReadyCuts} onClick={() => setReadyCutsModalOpen(false)}>✕</button>
                </div>
                <form onSubmit={handleConfirmReadyCutsModal}>
                  <div className="ready-cuts-order-modal-body">
                    <div className="form-group">
                      <label>Nome do job</label>
                      <input
                        type="text"
                        value={readyCutsJobName}
                        onChange={(e) => setReadyCutsJobName(e.target.value)}
                        placeholder="Ex: Parkour radical"
                        required
                      />
                    </div>
                    <p className="form-hint">Ordem na edição do vídeo longo (fade 0,5s entre clipes). Arraste não disponível — use ↑ ↓.</p>
                    <ul className="ready-cuts-order-list">
                      {readyCutsModalFiles.map((f, idx) => (
                        <li key={`${f.name}-${idx}`} className="ready-cuts-order-item">
                          <span className="ready-cuts-order-num">{idx + 1}</span>
                          <span className="ready-cuts-order-name">{f.name}</span>
                          <span className="ready-cuts-order-actions">
                            <button type="button" disabled={idx === 0 || creatingReadyCuts} onClick={() => moveReadyCutModal(idx, -1)} title="Subir">↑</button>
                            <button type="button" disabled={idx === readyCutsModalFiles.length - 1 || creatingReadyCuts} onClick={() => moveReadyCutModal(idx, 1)} title="Descer">↓</button>
                          </span>
                        </li>
                      ))}
                    </ul>
                    <div className="form-group">
                      <label>Transcrição e legenda</label>
                      <select
                        value={readyCutsTranscribe ? 'yes' : 'no'}
                        onChange={(e) => setReadyCutsTranscribe(e.target.value === 'yes')}
                      >
                        <option value="yes">Sim (Whisper + legenda nos vídeos)</option>
                        <option value="no">Não (títulos só a partir do nome do job)</option>
                      </select>
                    </div>
                    <div className="form-group">
                      <label>Idioma dos títulos (IA)</label>
                      <select
                        value={readyCutsTitlesLanguage}
                        onChange={(e) => setReadyCutsTitlesLanguage(e.target.value)}
                        disabled={!readyCutsTranscribe}
                        title={!readyCutsTranscribe ? 'Com transcrição desligada, os títulos seguem o nome do job (sem IA de títulos).' : undefined}
                      >
                        <option value="pt">Português (BR)</option>
                        <option value="en">English</option>
                      </select>
                      <p className="form-hint">Só aplica quando a transcrição está ativa; a LLM gera títulos só neste idioma.</p>
                    </div>
                    <div className="form-group">
                      <label>Criar vídeo longo (horizontal)</label>
                      <select
                        value={readyCutsLongVideo ? 'yes' : 'no'}
                        onChange={(e) => setReadyCutsLongVideo(e.target.value === 'yes')}
                      >
                        <option value="no">Não</option>
                        <option value="yes">Sim (junta os clipes com fade antes de finalizar os shorts)</option>
                      </select>
                    </div>
            <div className="form-group">
              <label>Formato final dos shorts</label>
              <select
                value={jobVerticalMode}
                onChange={(e) => setJobVerticalMode(e.target.value)}
              >
                <option value="zoom_crop">Zoom e corte</option>
                <option value="frame_center">Enquadrar e centralizar</option>
              </select>
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label>Deseja adicionar overlay?</label>
                        <select
                          value={readyCutsLongOverlayEnabled ? 'yes' : 'no'}
                          onChange={(e) => {
                            const yes = e.target.value === 'yes'
                            setReadyCutsLongOverlayEnabled(yes)
                            if (!yes) setReadyCutsLongOverlayAssetId('')
                          }}
                        >
                          <option value="no">Não</option>
                          <option value="yes">Sim</option>
                        </select>
                      </div>
                      <div className="form-group">
                        <label>Selecione o overlay</label>
                        <select
                          value={readyCutsLongOverlayAssetId}
                          onChange={(e) => setReadyCutsLongOverlayAssetId(e.target.value)}
                          disabled={!readyCutsLongOverlayEnabled}
                        >
                          <option value="">
                            {readyCutsLongOverlayEnabled ? 'Escolha…' : 'Marque Sim acima'}
                          </option>
                          {longOverlayAssets.map((a) => (
                            <option key={a.id} value={a.id}>{a.label || `Overlay #${a.id}`}</option>
                          ))}
                        </select>
                        {readyCutsLongOverlayEnabled && longOverlayAssets.length === 0 && (
                          <span className="form-hint">
                            Cadastre overlays em Mídias da marca → Adicionar overlay (vídeo longo).
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="form-hint">Overlay: só em cortes longos horizontais (PNG/JPG/MP4 na lateral direita).</p>
                    {(viewMode === 'factory' ? readyCutsBrandId : activeBrandId) ? (
                      <div className="form-row" style={{ marginTop: '0.75rem' }}>
                        <div className="form-group">
                          <label>Vídeo longo com legenda</label>
                          <select
                            value={readyCutsLongSubs ? 'yes' : 'no'}
                            onChange={async (e) => {
                              const v = e.target.value === 'yes'
                              setReadyCutsLongSubs(v)
                              await persistReadyCutsLongVideo('long_video_subtitles_enabled', v)
                            }}
                          >
                            <option value="no">Não</option>
                            <option value="yes">Sim</option>
                          </select>
                        </div>
                        <div className="form-group">
                          <label>Inserir logo no vídeo longo</label>
                          <select
                            value={readyCutsLongLogo ? 'yes' : 'no'}
                            onChange={async (e) => {
                              const v = e.target.value === 'yes'
                              setReadyCutsLongLogo(v)
                              await persistReadyCutsLongVideo('long_video_logo_enabled', v)
                            }}
                          >
                            <option value="no">Não</option>
                            <option value="yes">Sim</option>
                          </select>
                        </div>
                      </div>
                    ) : null}
                  </div>
                  <div className="ready-cuts-order-modal-footer">
                    <button type="button" onClick={() => !creatingReadyCuts && setReadyCutsModalOpen(false)}>Cancelar</button>
                    <button
                      type="submit"
                      disabled={
                        creatingReadyCuts
                        || !readyCutsModalFiles.length
                        || (viewMode === 'factory' && !readyCutsBrandId)
                      }
                    >
                      {creatingReadyCuts ? 'Enviando...' : 'OK — enviar job'}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}
        </section>
      )}

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
          {viewMode === 'factory' && factoryId && factoryBrandIds.length >= 1 ? (
            <div className="form-group">
              <label>Direcionamento dos cortes</label>
              <select
                value={targetBrandId}
                onChange={(e) => setTargetBrandId(e.target.value)}
              >
                <option value="">Por tema (IA)</option>
                <option value="distribute">Distribuir pelas Brands</option>
                {factoryBrands.map((b) => (
                  <option key={b.id} value={b.id}>
                    Direcionar para {b.name || `Brand #${b.id}`}
                  </option>
                ))}
              </select>
              <span className="form-hint">
                Por tema: usa categoria da IA. Distribuir: envia para a brand com menos vídeos no banco. Direcionar: todos para a brand escolhida.
              </span>
            </div>
          ) : null}
          <div className="form-group">
            <label>Modo de análise</label>
            <select
              value={promptVersion}
              onChange={(e) => setPromptVersion(e.target.value)}
            >
              <option value="educational">Educacional (PT, shorts 2–3 min)</option>
              <option value="viral">Viral (PT, shorts 30–60 seg)</option>
              <option value="viral_long">Viral longo (PT, shorts 90–160 seg)</option>
              <option value="educational_en">Educacional (EN, 2–3 min)</option>
              <option value="viral_en">Viral (EN, 30–60 seg)</option>
              <option value="viral_long_en">Viral longo (EN, shorts 90–160 seg)</option>
              <option value="viral_translate">Viral Translate - EN to PT</option>
            </select>
            <span className="form-hint">PT: transcrição e títulos em português. EN: transcrição e títulos em inglês. Viral Translate: vídeo em EN, legendas em PT.</span>
          </div>
          <div className="form-group">
            <label>Formato final dos shorts</label>
            <select
              value={jobVerticalMode}
              onChange={(e) => setJobVerticalMode(e.target.value)}
            >
              <option value="zoom_crop">Zoom e corte</option>
              <option value="frame_center">Enquadrar e centralizar</option>
            </select>
            <span className="form-hint">Zoom: preenche a tela. Enquadrar: vídeo centralizado com bordas e logo.</span>
          </div>
          {viewMode === 'factory' ? (
            <div className="form-group">
              <label>Estilo da thumbnail</label>
              <span className="form-hint">
                No contexto Factory, fonte/cores da thumbnail são herdadas da Brand selecionada.
                Edite em Brands da Factory.
              </span>
            </div>
          ) : (
            <>
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
            </>
          )}
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
          <div className="form-row">
            <div className="form-group">
              <label>Deseja adicionar overlay?</label>
              <select
                value={longOverlayEnabled ? 'yes' : 'no'}
                onChange={(e) => {
                  const yes = e.target.value === 'yes'
                  setLongOverlayEnabled(yes)
                  if (!yes) setLongOverlayAssetId('')
                }}
              >
                <option value="no">Não</option>
                <option value="yes">Sim</option>
              </select>
              <span className="form-hint">Sobreposto à direita nos vídeos longos (16:9).</span>
            </div>
            <div className="form-group">
              <label>Selecione o overlay</label>
              <select
                value={longOverlayAssetId}
                onChange={(e) => setLongOverlayAssetId(e.target.value)}
                disabled={!longOverlayEnabled}
              >
                <option value="">
                  {longOverlayEnabled ? 'Escolha…' : 'Marque Sim acima'}
                </option>
                {longOverlayAssets.map((a) => (
                  <option key={a.id} value={a.id}>{a.label || `Overlay #${a.id}`}</option>
                ))}
              </select>
              {longOverlayEnabled && longOverlayAssets.length === 0 && (
                <span className="form-hint">
                  Nenhum overlay cadastrado. Em <strong>Mídias da marca</strong> → <strong>Adicionar overlay (vídeo longo)</strong>, envie PNG, JPG ou MP4.
                </span>
              )}
            </div>
          </div>
          {activeBrandId ? (
            <div className="form-row">
              <div className="form-group">
                <label>Vídeo longo com legenda</label>
                <select
                  value={longVideoSubtitles ? 'yes' : 'no'}
                  onChange={async (e) => {
                    const v = e.target.value === 'yes'
                    setLongVideoSubtitles(v)
                    await persistLongVideoPreference('long_video_subtitles_enabled', v)
                  }}
                >
                  <option value="no">Não</option>
                  <option value="yes">Sim</option>
                </select>
                <span className="form-hint">Só afeta cortes longos 16:9 na finalização (padrão: não).</span>
              </div>
              <div className="form-group">
                <label>Inserir logo no vídeo longo</label>
                <select
                  value={longVideoLogo ? 'yes' : 'no'}
                  onChange={async (e) => {
                    const v = e.target.value === 'yes'
                    setLongVideoLogo(v)
                    await persistLongVideoPreference('long_video_logo_enabled', v)
                  }}
                >
                  <option value="no">Não</option>
                  <option value="yes">Sim</option>
                </select>
                <span className="form-hint">Logo da marca em longos 16:9 (padrão: não).</span>
              </div>
            </div>
          ) : null}
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
        {displayAnalyses.length === 0 ? (
          <p className="empty-msg">Nenhum job ainda.</p>
        ) : (
          <div className="analyses-list">
            {displayAnalyses.map((a) => (
              <div key={a.id} className="analysis-wrapper">
                <div
                  className={`analysis-card ${expandedId === a.id ? 'selected' : ''}`}
                  onClick={() => setExpandedId(expandedId === a.id ? null : a.id)}
                >
                  <div className="analysis-header">
                    <span className="analysis-name">
                      {a.name || `Job #${a.id}`}
                      {viewMode === 'factory' && (
                        <span className="analysis-direction">
                          {' '}({a.factory_name ? `${a.factory_name} · ` : ''}{a.target_brand_name || 'Todos'})
                        </span>
                      )}
                    </span>
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
                              <option value="zoom_crop">Zoom e corte</option>
                              <option value="frame_center">Enquadrar e centralizar</option>
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
                        Preferências por marca (vídeos longos 16:9). Ajuste também em &quot;Gerar cortes&quot; acima.
                      </p>
                      <div className="horizontal-options-fields">
                        <label>
                          <span>Vídeo longo com legenda</span>
                          <select
                            value={longVideoSubtitles ? 'yes' : 'no'}
                            onChange={async (e) => {
                              const v = e.target.value === 'yes'
                              setLongVideoSubtitles(v)
                              await persistLongVideoPreference('long_video_subtitles_enabled', v)
                            }}
                          >
                            <option value="no">Não</option>
                            <option value="yes">Sim</option>
                          </select>
                        </label>
                        <label>
                          <span>Inserir logo no vídeo longo</span>
                          <select
                            value={longVideoLogo ? 'yes' : 'no'}
                            onChange={async (e) => {
                              const v = e.target.value === 'yes'
                              setLongVideoLogo(v)
                              await persistLongVideoPreference('long_video_logo_enabled', v)
                            }}
                          >
                            <option value="no">Não</option>
                            <option value="yes">Sim</option>
                          </select>
                        </label>
                        {longVideoLogo && (
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
                      <h4>Estilo da legenda</h4>
                      <p className="form-hint">
                        Cortes curtos: legendas quando marcado no card. Vídeos longos 16:9: só queimam legenda se &quot;Vídeo longo com legenda&quot; estiver Sim na marca do corte.
                      </p>
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
        <p className="section-desc">
          Cortes agrupados por job. Clique para expandir. Use &quot;Editar nome&quot; para ajustar o título do vídeo
          (inventário e agendamentos pendentes são atualizados).
        </p>
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
                                <td className="finalized-title-cell">
                                  {titleEditCorteId === c.id ? (
                                    <div className="title-edit-row">
                                      <input
                                        type="text"
                                        className="title-edit-input"
                                        value={titleEditValue}
                                        onChange={(e) => setTitleEditValue(e.target.value)}
                                        maxLength={200}
                                        disabled={savingTitleId === c.id}
                                        onKeyDown={(e) => {
                                          if (e.key === 'Escape') {
                                            setTitleEditCorteId(null)
                                          }
                                        }}
                                      />
                                      <button
                                        type="button"
                                        className="btn-title-save"
                                        onClick={() => handleSaveFinalizedTitle(c)}
                                        disabled={savingTitleId === c.id}
                                      >
                                        {savingTitleId === c.id ? 'Salvando...' : 'Salvar'}
                                      </button>
                                      <button
                                        type="button"
                                        className="btn-title-cancel"
                                        onClick={() => setTitleEditCorteId(null)}
                                        disabled={savingTitleId === c.id}
                                      >
                                        Cancelar
                                      </button>
                                    </div>
                                  ) : (
                                    <div className="title-display-row">
                                      <span className="title-text">{c.suggestion?.title || '—'}</span>
                                      <button
                                        type="button"
                                        className="btn-edit-title"
                                        onClick={() => {
                                          setTitleEditCorteId(c.id)
                                          setTitleEditValue(c.suggestion?.title || '')
                                        }}
                                      >
                                        Editar nome
                                      </button>
                                    </div>
                                  )}
                                </td>
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
