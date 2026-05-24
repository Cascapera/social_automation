import { useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getBrands,
  getFactories,
  getBrandAssets,
  createBrandAsset,
  deleteBrandAsset,
  createBrand,
  updateBrand,
  deleteBrand,
  updateBrandYoutubeDescription,
  getBrandYoutubeCredentials,
  createBrandYoutubeCredential,
  updateBrandYoutubeCredential,
  deleteBrandYoutubeCredential,
  getBrandCategories,
} from '../api'
import './IntroOutro.css'

const ASSET_TYPES = [
  { id: 'LOGO', label: 'Logo' },
  { id: 'INTRO', label: 'Intro' },
  { id: 'OUTRO', label: 'Outro' },
  { id: 'CTA', label: 'CTA' },
  { id: 'ANIMATION', label: 'Animação overlay (PNG/GIF)' },
  { id: 'THUMB_SHORT', label: 'Thumb Shorts' },
  { id: 'THUMB_LONG', label: 'Thumb Longs' },
]

/** Fuso fixo do scheduler (sem edição na UI). */
const SCHEDULER_TIMEZONE_DEFAULT = 'America/Sao_Paulo'

const SCHEDULER_DEFAULTS = {
  scheduler_enabled: true,
  scheduler_paused: false,
  base_start_time: '09:00',
  base_end_time: '21:00',
  start_jitter_minutes: 0,
  end_jitter_minutes: 0,
  daily_min_posts: 3,
  daily_max_posts: 4,
  daily_min_long_posts: 0,
  daily_max_long_posts: 1,
  min_gap_minutes: 60,
  max_gap_minutes: 360,
  active_weekdays: [0, 1, 2, 3, 4, 5, 6],
}

const WEEKDAY_LABELS = [
  { v: 0, label: 'Seg' },
  { v: 1, label: 'Ter' },
  { v: 2, label: 'Qua' },
  { v: 3, label: 'Qui' },
  { v: 4, label: 'Sex' },
  { v: 5, label: 'Sáb' },
  { v: 6, label: 'Dom' },
]

function timeToInput(v, fallback = '09:00') {
  if (v == null || v === '') return fallback
  const s = String(v)
  return s.length >= 5 ? s.slice(0, 5) : fallback
}

function timeToApi(hhmm) {
  if (!hhmm || typeof hhmm !== 'string') return null
  const t = hhmm.slice(0, 5)
  if (!/^\d{2}:\d{2}$/.test(t)) return null
  return `${t}:00`
}

function weekdaysFromApi(wd) {
  if (!Array.isArray(wd) || wd.length === 0) return [0, 1, 2, 3, 4, 5, 6]
  return [...new Set(wd.map(Number))]
    .filter((n) => n >= 0 && n <= 6)
    .sort((a, b) => a - b)
}

function weekdaysToApi(wd) {
  if (!Array.isArray(wd) || wd.length === 0) return []
  const sorted = [...new Set(wd)].sort((a, b) => a - b)
  if (sorted.length === 7 && sorted.every((v, i) => v === i)) return []
  return sorted
}

/** API pode devolver "18:00:00"; inputs usam "18:MM". Unifica para HH:MM nos chips e no PATCH. */
function normalizeLongSlotTimesList(raw) {
  if (!Array.isArray(raw)) return []
  const seen = new Set()
  const out = []
  for (const x of raw) {
    const s = String(x || '').trim()
    if (!s) continue
    const hhmm = s.length >= 5 ? s.slice(0, 5) : s
    if (!/^\d{1,2}:\d{2}$/.test(hhmm)) continue
    if (!seen.has(hhmm)) {
      seen.add(hhmm)
      out.push(hhmm)
    }
  }
  return out.sort()
}

function scheduleFromBrand(b) {
  if (!b) return { ...SCHEDULER_DEFAULTS }
  return {
    scheduler_enabled: b.scheduler_enabled !== false,
    scheduler_paused: !!b.scheduler_paused,
    base_start_time: timeToInput(b.base_start_time, '09:00'),
    base_end_time: timeToInput(b.base_end_time, '21:00'),
    start_jitter_minutes: Math.max(0, Number(b.start_jitter_minutes ?? 0)),
    end_jitter_minutes: Math.max(0, Number(b.end_jitter_minutes ?? 0)),
    daily_min_posts: Math.max(0, Number(b.daily_min_posts ?? 3)),
    daily_max_posts: Math.max(0, Number(b.daily_max_posts ?? 4)),
    daily_min_long_posts: Math.max(0, Number(b.daily_min_long_posts ?? 0)),
    daily_max_long_posts: Math.max(0, Number(b.daily_max_long_posts ?? 1)),
    min_gap_minutes: Math.max(0, Number(b.min_gap_minutes ?? 60)),
    max_gap_minutes: Math.max(0, Number(b.max_gap_minutes ?? 360)),
    active_weekdays: weekdaysFromApi(b.active_weekdays),
  }
}

/** Campos do plano diário (API). Inclui `short_slot_times: []` para não usar horários fixos legados. */
function scheduleToPayload(s) {
  return {
    scheduler_timezone: SCHEDULER_TIMEZONE_DEFAULT,
    scheduler_enabled: !!s.scheduler_enabled,
    scheduler_paused: !!s.scheduler_paused,
    base_start_time: timeToApi(s.base_start_time),
    base_end_time: timeToApi(s.base_end_time),
    start_jitter_minutes: Number(s.start_jitter_minutes) || 0,
    end_jitter_minutes: Number(s.end_jitter_minutes) || 0,
    daily_min_posts: Number(s.daily_min_posts) || 0,
    daily_max_posts: Number(s.daily_max_posts) || 0,
    daily_min_long_posts: Number(s.daily_min_long_posts) || 0,
    daily_max_long_posts: Number(s.daily_max_long_posts) || 0,
    min_gap_minutes: Number(s.min_gap_minutes) || 0,
    max_gap_minutes: Number(s.max_gap_minutes) || 0,
    active_weekdays: weekdaysToApi(s.active_weekdays),
    short_slot_times: [],
  }
}

function SchedulerPlanFields({ schedule, setSchedule }) {
  const set = (key, val) => setSchedule((prev) => ({ ...prev, [key]: val }))
  const toggleDay = (d) => {
    setSchedule((prev) => {
      const w = [...prev.active_weekdays]
      const i = w.indexOf(d)
      if (i >= 0) {
        if (w.length <= 1) return prev
        w.splice(i, 1)
      } else {
        w.push(d)
        w.sort((a, b) => a - b)
      }
      return { ...prev, active_weekdays: w }
    })
  }
  return (
    <div className="scheduler-plan-fields">
      <div className="form-row" style={{ alignItems: 'center' }}>
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={schedule.scheduler_enabled}
            onChange={(e) => set('scheduler_enabled', e.target.checked)}
          />
          Scheduler ativo
        </label>
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={schedule.scheduler_paused}
            onChange={(e) => set('scheduler_paused', e.target.checked)}
          />
          Pausado (mantém configuração)
        </label>
      </div>
      <p className="form-hint">Fuso horário do agendamento: {SCHEDULER_TIMEZONE_DEFAULT} (fixo).</p>
      <div className="form-row">
        <div className="form-group">
          <label>Início da janela</label>
          <input type="time" value={schedule.base_start_time} onChange={(e) => set('base_start_time', e.target.value)} />
        </div>
        <div className="form-group">
          <label>Fim da janela</label>
          <input type="time" value={schedule.base_end_time} onChange={(e) => set('base_end_time', e.target.value)} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Jitter início (min)</label>
          <input
            type="number"
            min={0}
            value={schedule.start_jitter_minutes}
            onChange={(e) => set('start_jitter_minutes', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Jitter fim (min)</label>
          <input
            type="number"
            min={0}
            value={schedule.end_jitter_minutes}
            onChange={(e) => set('end_jitter_minutes', e.target.value)}
          />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Mín. posts/dia (shorts + longos)</label>
          <input
            type="number"
            min={0}
            value={schedule.daily_min_posts}
            onChange={(e) => set('daily_min_posts', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Máx. posts/dia</label>
          <input
            type="number"
            min={0}
            value={schedule.daily_max_posts}
            onChange={(e) => set('daily_max_posts', e.target.value)}
          />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Mín. longos/dia</label>
          <input
            type="number"
            min={0}
            value={schedule.daily_min_long_posts}
            onChange={(e) => set('daily_min_long_posts', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Máx. longos/dia</label>
          <input
            type="number"
            min={0}
            value={schedule.daily_max_long_posts}
            onChange={(e) => set('daily_max_long_posts', e.target.value)}
          />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Intervalo mín. entre posts (min)</label>
          <input
            type="number"
            min={0}
            value={schedule.min_gap_minutes}
            onChange={(e) => set('min_gap_minutes', e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Intervalo máx. entre posts (min)</label>
          <input
            type="number"
            min={0}
            value={schedule.max_gap_minutes}
            onChange={(e) => set('max_gap_minutes', e.target.value)}
          />
        </div>
      </div>
      <div className="form-group">
        <label>Dias ativos</label>
        <p className="form-hint">Lista vazia na API = todos os dias; aqui, marcar os 7 equivale a “todos”.</p>
        <div className="weekday-chips">
          {WEEKDAY_LABELS.map(({ v, label }) => (
            <button
              key={v}
              type="button"
              className={`weekday-chip ${schedule.active_weekdays.includes(v) ? 'weekday-chip--on' : ''}`}
              onClick={() => toggleDay(v)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function ColorField({ label, value, onChange }) {
  return (
    <div className="form-group color-field">
      <label>{label}</label>
      <div className="color-input-wrap">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="#RRGGBB"
          maxLength={7}
        />
        <input
          type="color"
          value={value || '#000000'}
          onChange={(e) => onChange((e.target.value || '').toUpperCase())}
          className="color-picker"
          title="Escolher cor"
        />
        <span
          className="color-preview-chip"
          style={{ backgroundColor: value || '#000000' }}
          title={value || '#000000'}
        />
      </div>
    </div>
  )
}

export default function IntroOutro() {
  const {
    brandId,
    setBrandId,
    brands,
    factories,
    setFactories,
    viewMode,
    factoryId,
    refreshBrands,
  } = useBrand()
  const [assets, setAssets] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Form para adicionar
  const [assetType, setAssetType] = useState('INTRO')
  const [label, setLabel] = useState('')
  const [file, setFile] = useState(null)
  const [overlayLongLabel, setOverlayLongLabel] = useState('')
  const [overlayLongFile, setOverlayLongFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [newBrandName, setNewBrandName] = useState('')
  const [newBrandThemeCategory, setNewBrandThemeCategory] = useState('')
  const [newBrandFactoryId, setNewBrandFactoryId] = useState('')
  const [newBrandLogoFile, setNewBrandLogoFile] = useState(null)
  const [newBrandThumbnailFont, setNewBrandThumbnailFont] = useState('impact')
  const [newBrandBandColor, setNewBrandBandColor] = useState('#E12E20')
  const [newBrandTextColor, setNewBrandTextColor] = useState('#0A0A0A')
  const [newBrandEffectColor, setNewBrandEffectColor] = useState('#FFEBDC')
  const [newBrandDescriptionExtra, setNewBrandDescriptionExtra] = useState('')
  const [newBrandSchedule, setNewBrandSchedule] = useState(() => ({ ...SCHEDULER_DEFAULTS }))
  const [newBrandLongSlotTimes, setNewBrandLongSlotTimes] = useState([])
  const [newBrandLongSlotTimeInput, setNewBrandLongSlotTimeInput] = useState('20:00')
  const [newBrandVerticalMode, setNewBrandVerticalMode] = useState('zoom_crop')
  const [creatingBrand, setCreatingBrand] = useState(false)
  const [showNewBrand, setShowNewBrand] = useState(false)
  const [youtubeDescriptionExtra, setYoutubeDescriptionExtra] = useState('')
  const [youtubeMadeForKids, setYoutubeMadeForKids] = useState(false)
  const [savingYoutubeDescription, setSavingYoutubeDescription] = useState(false)
  const [editingBrand, setEditingBrand] = useState(false)
  const [deletingBrand, setDeletingBrand] = useState(false)
  const [editThemeCategory, setEditThemeCategory] = useState('')
  const [editBrandName, setEditBrandName] = useState('')
  const [brandCategories, setBrandCategories] = useState([])
  const [editThumbnailFont, setEditThumbnailFont] = useState('impact')
  const [editBandColor, setEditBandColor] = useState('#E12E20')
  const [editTextColor, setEditTextColor] = useState('#0A0A0A')
  const [editEffectColor, setEditEffectColor] = useState('#FFEBDC')
  const [editSchedule, setEditSchedule] = useState(() => ({ ...SCHEDULER_DEFAULTS }))
  const [editLongSlotTimes, setEditLongSlotTimes] = useState([])
  const [editVerticalMode, setEditVerticalMode] = useState('zoom_crop')
  const [newLongSlotTime, setNewLongSlotTime] = useState('20:00')
  // Upload-Post (TikTok, X, Instagram)
  const [uploadPostTiktokEnabled, setUploadPostTiktokEnabled] = useState(false)
  const [uploadPostTiktokExtra, setUploadPostTiktokExtra] = useState('')
  const [uploadPostXEnabled, setUploadPostXEnabled] = useState(false)
  const [uploadPostXExtra, setUploadPostXExtra] = useState('')
  const [uploadPostInstagramEnabled, setUploadPostInstagramEnabled] = useState(false)
  const [uploadPostInstagramExtra, setUploadPostInstagramExtra] = useState('')
  const [uploadPostYoutubeEnabled, setUploadPostYoutubeEnabled] = useState(false)
  const [savingUploadPost, setSavingUploadPost] = useState(false)
  const [youtubeCredentials, setYoutubeCredentials] = useState([])
  const [youtubeCredentialSecrets, setYoutubeCredentialSecrets] = useState({})
  const [loadingYoutubeCredentials, setLoadingYoutubeCredentials] = useState(false)
  const [savingYoutubeCredentialId, setSavingYoutubeCredentialId] = useState(null)
  const [deletingYoutubeCredentialId, setDeletingYoutubeCredentialId] = useState(null)
  const [creatingYoutubeCredential, setCreatingYoutubeCredential] = useState(false)
  const [newYoutubeCredentialLabel, setNewYoutubeCredentialLabel] = useState('')
  const [newYoutubeCredentialOrder, setNewYoutubeCredentialOrder] = useState(1)
  const [newYoutubeCredentialClientId, setNewYoutubeCredentialClientId] = useState('')
  const [newYoutubeCredentialClientSecret, setNewYoutubeCredentialClientSecret] = useState('')
  const [newYoutubeCredentialRedirectUri, setNewYoutubeCredentialRedirectUri] = useState('')

  useEffect(() => {
    const fetcher = () => getBrands(viewMode === 'factory' && factoryId ? factoryId : null)
    refreshBrands(fetcher)
    if (!factories?.length) {
      getFactories().then(setFactories).catch(() => setFactories([]))
    }
  }, [viewMode, factoryId])

  useEffect(() => {
    if (viewMode === 'factory') {
      setNewBrandFactoryId(factoryId || '')
    }
  }, [viewMode, factoryId])

  useEffect(() => {
    if (brandId) {
      setLoading(true)
      getBrandAssets(brandId)
        .then(setAssets)
        .catch(() => setAssets([]))
        .finally(() => setLoading(false))
      setLoadingYoutubeCredentials(true)
      getBrandYoutubeCredentials(brandId)
        .then((items) => {
          setYoutubeCredentials(items || [])
          const highestOrder = Math.max(1, ...((items || []).map((i) => Number(i.order_index || 0))))
          setNewYoutubeCredentialOrder(highestOrder + 1)
        })
        .catch(() => setYoutubeCredentials([]))
        .finally(() => setLoadingYoutubeCredentials(false))
    } else {
      setAssets([])
      setLoading(false)
      setYoutubeCredentials([])
      setYoutubeCredentialSecrets({})
      setLoadingYoutubeCredentials(false)
    }
  }, [brandId])

  useEffect(() => {
    const selected = brands.find((b) => String(b.id) === String(brandId))
    setYoutubeDescriptionExtra(selected?.youtube_description_extra || '')
    setYoutubeMadeForKids(!!selected?.youtube_made_for_kids)
    setEditThemeCategory(selected?.theme_category || '')
    setEditBrandName(selected?.name || '')
    setEditThumbnailFont(selected?.thumbnail_font || 'impact')
    setEditBandColor(selected?.thumbnail_band_color || '#E12E20')
    setEditTextColor(selected?.thumbnail_text_color || '#0A0A0A')
    setEditEffectColor(selected?.thumbnail_effect_color || '#FFEBDC')
    setEditSchedule(scheduleFromBrand(selected))
    setEditLongSlotTimes(normalizeLongSlotTimesList(selected?.long_slot_times))
    setEditVerticalMode(selected?.vertical_mode || 'zoom_crop')
    setUploadPostTiktokEnabled(!!selected?.upload_post_tiktok_enabled)
    setUploadPostTiktokExtra(selected?.upload_post_tiktok_extra_description || '')
    setUploadPostXEnabled(!!selected?.upload_post_x_enabled)
    setUploadPostXExtra(selected?.upload_post_x_extra_description || '')
    setUploadPostInstagramEnabled(!!selected?.upload_post_instagram_enabled)
    setUploadPostInstagramExtra(selected?.upload_post_instagram_extra_description || '')
    setUploadPostYoutubeEnabled(!!selected?.upload_post_youtube_enabled)
  }, [brandId, brands])

  // Carrega categorias ativas da factory relevante (brand selecionada OU factory ativa OU
  // factory escolhida no cadastro de nova brand).
  useEffect(() => {
    const selected = brands.find((b) => String(b.id) === String(brandId))
    const fid =
      (selected && selected.factory) ||
      (viewMode === 'factory' ? factoryId : null) ||
      newBrandFactoryId ||
      null
    if (!fid) {
      setBrandCategories([])
      return
    }
    getBrandCategories(fid)
      .then((items) => setBrandCategories(items || []))
      .catch(() => setBrandCategories([]))
  }, [brandId, brands, viewMode, factoryId, newBrandFactoryId])

  async function handleAddOverlay(e) {
    e.preventDefault()
    if (!brandId || !overlayLongFile) {
      setError('Selecione a marca e um arquivo (PNG, JPG ou MP4)')
      return
    }
    setError('')
    setUploading(true)
    try {
      await createBrandAsset(brandId, 'OVERLAY_LONG', overlayLongFile, overlayLongLabel.trim())
      setOverlayLongFile(null)
      setOverlayLongLabel('')
      getBrandAssets(brandId).then(setAssets)
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  async function handleAdd(e) {
    e.preventDefault()
    if (!brandId || !file) {
      setError('Selecione a marca e um arquivo')
      return
    }
    setError('')
    setUploading(true)
    try {
      if (assetType === 'LOGO') {
        const logos = assets.filter((a) => a.asset_type === 'LOGO')
        for (const logo of logos) {
          await deleteBrandAsset(logo.id)
        }
      }
      if (assetType === 'THUMB_SHORT' || assetType === 'THUMB_LONG') {
        const existing = assets.filter((a) => a.asset_type === assetType)
        for (const a of existing) {
          await deleteBrandAsset(a.id)
        }
      }
      await createBrandAsset(brandId, assetType, file, label.trim())
      setFile(null)
      setLabel('')
      getBrandAssets(brandId).then(setAssets)
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  async function handleCreateBrand(e) {
    e.preventDefault()
    if (!newBrandName.trim()) {
      setError('Digite o nome da marca')
      return
    }
    const targetFactoryId = viewMode === 'factory' ? (factoryId || newBrandFactoryId) : newBrandFactoryId
    if (viewMode === 'factory' && !targetFactoryId) {
      setError('Selecione uma factory para cadastrar a brand.')
      return
    }
    if (viewMode === 'factory' && !newBrandThemeCategory) {
      setError('Selecione a categoria da brand na factory.')
      return
    }
    setError('')
    setCreatingBrand(true)
    try {
      const payload = {
        name: newBrandName.trim(),
        factory: targetFactoryId ? Number(targetFactoryId) : null,
        theme_category: newBrandThemeCategory || '',
        thumbnail_font: newBrandThumbnailFont,
        thumbnail_band_color: (newBrandBandColor || '').trim(),
        thumbnail_text_color: (newBrandTextColor || '').trim(),
        thumbnail_effect_color: (newBrandEffectColor || '').trim(),
        youtube_description_extra: newBrandDescriptionExtra || '',
        ...scheduleToPayload(newBrandSchedule),
        long_slot_times: normalizeLongSlotTimesList(newBrandLongSlotTimes),
        vertical_mode: newBrandVerticalMode || 'zoom_crop',
      }
      const b = await createBrand(payload)
      if (newBrandLogoFile) {
        await createBrandAsset(b.id, 'LOGO', newBrandLogoFile, 'logo principal')
      }
      const fetcher = () => getBrands(viewMode === 'factory' && factoryId ? factoryId : null)
      await refreshBrands(fetcher)
      setBrandId(String(b.id))
      setNewBrandName('')
      setNewBrandThemeCategory('')
      setNewBrandLogoFile(null)
      setNewBrandDescriptionExtra('')
      setNewBrandSchedule({ ...SCHEDULER_DEFAULTS })
      setShowNewBrand(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setCreatingBrand(false)
    }
  }

  async function handleDelete(asset) {
    if (!confirm(`Deletar "${asset.label || asset.asset_type}"?`)) return
    try {
      await deleteBrandAsset(asset.id)
      getBrandAssets(brandId).then(setAssets)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleSaveYoutubeDescription(e) {
    e.preventDefault()
    if (!brandId) return
    setSavingYoutubeDescription(true)
    setError('')
    try {
      await updateBrandYoutubeDescription(brandId, youtubeDescriptionExtra, youtubeMadeForKids)
      const fetcher = () => getBrands(viewMode === 'factory' && factoryId ? factoryId : null)
      await refreshBrands(fetcher)
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingYoutubeDescription(false)
    }
  }

  async function handleSaveBrandScheduling(e) {
    e.preventDefault()
    if (!brandId) return
    const trimmedName = (editBrandName || '').trim()
    if (!trimmedName) {
      setError('O nome da brand não pode ficar vazio.')
      return
    }
    setEditingBrand(true)
    setError('')
    try {
      await updateBrand(brandId, {
        name: trimmedName,
        theme_category: editThemeCategory || '',
        thumbnail_font: editThumbnailFont || 'impact',
        thumbnail_band_color: (editBandColor || '').trim(),
        thumbnail_text_color: (editTextColor || '').trim(),
        thumbnail_effect_color: (editEffectColor || '').trim(),
        ...scheduleToPayload(editSchedule),
        long_slot_times: normalizeLongSlotTimesList(editLongSlotTimes),
        vertical_mode: editVerticalMode || 'zoom_crop',
      })
      const fetcher = () => getBrands(viewMode === 'factory' && factoryId ? factoryId : null)
      await refreshBrands(fetcher)
    } catch (e) {
      setError(e.message)
    } finally {
      setEditingBrand(false)
    }
  }

  async function handleDeleteBrand() {
    if (!brandId) return
    const currentBrand = brands.find((b) => String(b.id) === String(brandId))
    const label = currentBrand?.name || `#${brandId}`
    if (!confirm(`Excluir a brand "${label}"?\nEssa ação remove também mídias e dados vinculados.`)) {
      return
    }
    setDeletingBrand(true)
    setError('')
    try {
      await deleteBrand(brandId)
      setBrandId('')
      const fetcher = () => getBrands(viewMode === 'factory' && factoryId ? factoryId : null)
      await refreshBrands(fetcher)
      setAssets([])
    } catch (e) {
      setError(e.message)
    } finally {
      setDeletingBrand(false)
    }
  }

  function handleYoutubeCredentialFieldChange(id, field, value) {
    setYoutubeCredentials((prev) =>
      prev.map((cred) => (cred.id === id ? { ...cred, [field]: value } : cred)),
    )
  }

  async function handleCreateYoutubeCredential(e) {
    e.preventDefault()
    if (!brandId) return
    if (!newYoutubeCredentialClientId.trim()) {
      setError('Informe o Google Client ID da nova credencial.')
      return
    }
    setCreatingYoutubeCredential(true)
    setError('')
    try {
      await createBrandYoutubeCredential({
        brand: Number(brandId),
        label: newYoutubeCredentialLabel || '',
        order_index: Number(newYoutubeCredentialOrder || 1),
        client_id: newYoutubeCredentialClientId || '',
        client_secret: newYoutubeCredentialClientSecret || '',
        redirect_uri: newYoutubeCredentialRedirectUri || '',
        is_active: true,
      })
      const items = await getBrandYoutubeCredentials(brandId)
      setYoutubeCredentials(items || [])
      const highestOrder = Math.max(1, ...((items || []).map((i) => Number(i.order_index || 0))))
      setNewYoutubeCredentialOrder(highestOrder + 1)
      setNewYoutubeCredentialLabel('')
      setNewYoutubeCredentialClientId('')
      setNewYoutubeCredentialClientSecret('')
      setNewYoutubeCredentialRedirectUri('')
    } catch (e) {
      setError(e.message)
    } finally {
      setCreatingYoutubeCredential(false)
    }
  }

  async function handleSaveYoutubeCredential(cred) {
    if (!cred?.id) return
    setSavingYoutubeCredentialId(cred.id)
    setError('')
    try {
      const payload = {
        label: cred.label || '',
        order_index: Number(cred.order_index || 1),
        is_active: !!cred.is_active,
        client_id: cred.client_id || '',
        redirect_uri: cred.redirect_uri || '',
      }
      const newSecret = (youtubeCredentialSecrets[cred.id] || '').trim()
      if (newSecret) {
        payload.client_secret = newSecret
      }
      await updateBrandYoutubeCredential(cred.id, payload)
      setYoutubeCredentialSecrets((prev) => ({ ...prev, [cred.id]: '' }))
      const items = await getBrandYoutubeCredentials(brandId)
      setYoutubeCredentials(items || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingYoutubeCredentialId(null)
    }
  }

  async function handleSaveUploadPost(e) {
    e.preventDefault()
    if (!brandId) return
    setSavingUploadPost(true)
    setError('')
    try {
      await updateBrand(brandId, {
        upload_post_tiktok_enabled: uploadPostTiktokEnabled,
        upload_post_tiktok_extra_description: uploadPostTiktokExtra || '',
        upload_post_x_enabled: uploadPostXEnabled,
        upload_post_x_extra_description: uploadPostXExtra || '',
        upload_post_instagram_enabled: uploadPostInstagramEnabled,
        upload_post_instagram_extra_description: uploadPostInstagramExtra || '',
        upload_post_youtube_enabled: uploadPostYoutubeEnabled,
      })
      const fetcher = () => getBrands(viewMode === 'factory' && factoryId ? factoryId : null)
      await refreshBrands(fetcher)
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingUploadPost(false)
    }
  }

  async function handleDeleteYoutubeCredential(cred) {
    if (!cred?.id) return
    if (!confirm(`Excluir credencial "${cred.label || `#${cred.id}`}"?`)) return
    setDeletingYoutubeCredentialId(cred.id)
    setError('')
    try {
      await deleteBrandYoutubeCredential(cred.id)
      const items = await getBrandYoutubeCredentials(brandId)
      setYoutubeCredentials(items || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setDeletingYoutubeCredentialId(null)
    }
  }

  const typeLabel = (id) => {
    if (id === 'OVERLAY_LONG') return 'Overlay vídeo longo (direita)'
    return ASSET_TYPES.find((t) => t.id === id)?.label || id
  }

  return (
    <div className="intro-outro">
      <h1>{viewMode === 'factory' ? 'Brands' : 'Mídias da marca'}</h1>
      <p className="page-desc">
        {viewMode === 'factory'
          ? 'Cadastre brands da factory com categoria, regras de agendamento e identidade visual. O logo é enviado no mesmo cadastro.'
          : 'Adicione logo, vídeos de intro, outro e CTA. O logo é usado automaticamente nos cortes automáticos quando a opção "Enquadrar e centralizar" for selecionada.'}
      </p>

      {showNewBrand && (
        <div className="add-form">
          <h2>{viewMode === 'factory' ? 'Nova brand da factory' : 'Nova marca'}</h2>
          <form onSubmit={handleCreateBrand} className="brand-create-form">
            <div className="form-section">
              <h3>Dados da Brand</h3>
              <div className="form-row">
                <div className="form-group">
                  <label>Nome da brand (canal)</label>
                  <input
                    type="text"
                    value={newBrandName}
                    onChange={(e) => setNewBrandName(e.target.value)}
                    placeholder="Ex: Canal Histórias BR"
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Factory</label>
                  <select
                    value={viewMode === 'factory' ? (factoryId || '') : newBrandFactoryId}
                    onChange={(e) => setNewBrandFactoryId(e.target.value)}
                    disabled={viewMode === 'factory'}
                    required={viewMode === 'factory'}
                  >
                    <option value="">Selecione</option>
                    {(factories || []).map((f) => (
                      <option key={f.id} value={f.id}>{f.name}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>Categoria da brand</label>
                  <select
                    value={newBrandThemeCategory}
                    onChange={(e) => setNewBrandThemeCategory(e.target.value)}
                    required={viewMode === 'factory'}
                  >
                    <option value="">Selecione</option>
                    {brandCategories.map((t) => (
                      <option key={t.id} value={t.code}>{t.label}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <div className="form-section">
              <h3>Identidade Visual</h3>
              <div className="form-row">
                <div className="form-group">
                  <label>Logo da brand (opcional)</label>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(e) => setNewBrandLogoFile(e.target.files?.[0] || null)}
                  />
                </div>
                <div className="form-group">
                  <label>Fonte da thumbnail</label>
                  <select
                    value={newBrandThumbnailFont}
                    onChange={(e) => setNewBrandThumbnailFont(e.target.value)}
                  >
                    <option value="impact">Impact</option>
                    <option value="anton">Anton</option>
                    <option value="bebas">Bebas Neue</option>
                    <option value="montserrat">Montserrat ExtraBold</option>
                  </select>
                </div>
              </div>
              <div className="form-row">
                <ColorField label="Cor faixa thumbnail" value={newBrandBandColor} onChange={setNewBrandBandColor} />
                <ColorField label="Cor texto thumbnail" value={newBrandTextColor} onChange={setNewBrandTextColor} />
                <ColorField label="Cor efeito thumbnail" value={newBrandEffectColor} onChange={setNewBrandEffectColor} />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Modo de edição de shorts</label>
                  <select value={newBrandVerticalMode} onChange={(e) => setNewBrandVerticalMode(e.target.value)}>
                    <option value="zoom_crop">Zoom e corte</option>
                    <option value="frame_center">Enquadrar e centralizar</option>
                  </select>
                  <p className="form-hint">Zoom preenche a tela; Enquadrar adiciona bordas e logo.</p>
                </div>
              </div>
            </div>

            <div className="form-section">
              <h3>Descrição padrão</h3>
              <div className="form-group">
                <label>Texto extra da descrição</label>
                <textarea
                  rows={8}
                  value={newBrandDescriptionExtra}
                  onChange={(e) => setNewBrandDescriptionExtra(e.target.value)}
                  placeholder="Cole aqui o texto formatado da descrição para visualizar como ficará..."
                  className="brand-description-preview"
                />
              </div>
            </div>

            <div className="form-section">
              <h3>Regras de Agendamento</h3>
              <p className="form-hint">
                Plano diário: janela operacional, volumes e jitter. Os horários de publicação são gerados dentro da janela (não são fixos).
              </p>
              <SchedulerPlanFields schedule={newBrandSchedule} setSchedule={setNewBrandSchedule} />
              <div className="form-group">
                <label>Horários alvo dos vídeos longos (opcional)</label>
                <p className="form-hint">Se preenchido, o plano ancora os longos perto destes horários (± jitter). Vazio = longos distribuídos como os shorts, só pela janela.</p>
                <div className="short-slots-list">
                  {(newBrandLongSlotTimes || []).map((t, i) => (
                    <span key={i} className="short-slot-chip">
                      {t}
                      <button type="button" onClick={() => setNewBrandLongSlotTimes((prev) => prev.filter((_, j) => j !== i))} title="Remover">×</button>
                    </span>
                  ))}
                  <div className="short-slot-add">
                    <input type="time" value={newBrandLongSlotTimeInput} onChange={(e) => setNewBrandLongSlotTimeInput(e.target.value)} />
                    <button type="button" onClick={() => {
                      const v = newBrandLongSlotTimeInput?.slice(0, 5)
                      if (v && !newBrandLongSlotTimes.includes(v)) setNewBrandLongSlotTimes((prev) => [...prev, v].sort())
                    }}>Adicionar</button>
                  </div>
                </div>
              </div>
            </div>

            <div className="form-actions-row">
              <button type="submit" disabled={creatingBrand}>
                {creatingBrand ? 'Cadastrando...' : 'Cadastrar brand'}
              </button>
              <button type="button" onClick={() => setShowNewBrand(false)}>Cancelar</button>
            </div>
          </form>
        </div>
      )}

      {!brandId ? (
        <p className="form-hint">
          {viewMode === 'factory'
            ? 'Selecione uma brand da factory no menu lateral para gerenciar mídias e configurações.'
            : 'Selecione uma marca no menu à esquerda.'}
          {brands.length === 0 && ' Nenhuma marca cadastrada. '}
          {!showNewBrand ? (
            <button type="button" className="btn-link" onClick={() => setShowNewBrand(true)}>
              + {viewMode === 'factory' ? 'Nova brand' : 'Nova marca'}
            </button>
          ) : (
            <span />
          )}
        </p>
      ) : (
        <p className="form-hint">
          {viewMode === 'factory' ? 'Brand' : 'Marca'}: {brands.find((b) => String(b.id) === brandId)?.name || brandId}
          {' '}<button type="button" className="btn-link" onClick={() => setShowNewBrand((v) => !v)}>
            {showNewBrand ? 'Ocultar cadastro' : `+ ${viewMode === 'factory' ? 'Nova brand' : 'Nova marca'}`}
          </button>
        </p>
      )}

      {error && <div className="form-error">{error}</div>}

      {brandId && (
        <>
          <div className="add-form">
            <h2>Configuração de agendamento da brand</h2>
            <p className="form-hint">
              Defina a janela operacional, volumes diários e dias ativos. Os shorts usam horários gerados na janela. Se preencher os horários de longos abaixo, cada longo ancora nesse horário (com pequeno jitter); se deixar vazio, longos e shorts seguem só a janela.
            </p>
            <form onSubmit={handleSaveBrandScheduling} className="brand-create-form">
              <div className="form-row">
                <div className="form-group">
                  <label>Nome da brand</label>
                  <input
                    type="text"
                    value={editBrandName}
                    onChange={(e) => setEditBrandName(e.target.value)}
                    placeholder="Nome da brand"
                    required
                  />
                  <p className="form-hint">Renomear não altera o identificador interno da brand (brand_id) — canais e integrações existentes continuam válidos.</p>
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Fonte da thumbnail</label>
                  <select value={editThumbnailFont} onChange={(e) => setEditThumbnailFont(e.target.value)}>
                    <option value="impact">Impact</option>
                    <option value="anton">Anton</option>
                    <option value="bebas">Bebas Neue</option>
                    <option value="montserrat">Montserrat ExtraBold</option>
                  </select>
                </div>
                <ColorField label="Cor faixa thumbnail" value={editBandColor} onChange={setEditBandColor} />
                <ColorField label="Cor texto thumbnail" value={editTextColor} onChange={setEditTextColor} />
                <ColorField label="Cor efeito thumbnail" value={editEffectColor} onChange={setEditEffectColor} />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Categoria da brand</label>
                  <select
                    value={editThemeCategory}
                    onChange={(e) => setEditThemeCategory(e.target.value)}
                    required={viewMode === 'factory'}
                  >
                    <option value="">Selecione</option>
                    {brandCategories.map((t) => (
                      <option key={t.id} value={t.code}>{t.label}</option>
                    ))}
                  </select>
                  <p className="form-hint">Categorias são gerenciadas em &quot;Factory Config&quot;.</p>
                </div>
                <div className="form-group">
                  <label>Modo de edição de shorts</label>
                  <select value={editVerticalMode} onChange={(e) => setEditVerticalMode(e.target.value)}>
                    <option value="zoom_crop">Zoom e corte</option>
                    <option value="frame_center">Enquadrar e centralizar</option>
                  </select>
                  <p className="form-hint">Zoom preenche a tela; Enquadrar adiciona bordas e logo.</p>
                </div>
              </div>
              <SchedulerPlanFields schedule={editSchedule} setSchedule={setEditSchedule} />
              <div className="form-group">
                <label>Horários alvo dos vídeos longos (opcional)</label>
                <p className="form-hint">Se preenchido, o plano ancora os longos perto destes horários (± jitter). Vazio = longos distribuídos como os shorts, só pela janela.</p>
                <div className="short-slots-list">
                  {(editLongSlotTimes || []).map((t, i) => (
                    <span key={i} className="short-slot-chip">
                      {t}
                      <button type="button" onClick={() => setEditLongSlotTimes((prev) => prev.filter((_, j) => j !== i))} title="Remover">×</button>
                    </span>
                  ))}
                  <div className="short-slot-add">
                    <input type="time" value={newLongSlotTime} onChange={(e) => setNewLongSlotTime(e.target.value)} />
                    <button type="button" onClick={() => {
                      const v = newLongSlotTime?.slice(0, 5)
                      if (v && !editLongSlotTimes.includes(v)) setEditLongSlotTimes((prev) => [...prev, v].sort())
                    }}>Adicionar</button>
                  </div>
                </div>
              </div>
              <div className="form-actions-row">
                <button type="submit" disabled={editingBrand}>
                  {editingBrand ? 'Salvando...' : 'Salvar configuração da brand'}
                </button>
                <button
                  type="button"
                  className="danger-btn"
                  onClick={handleDeleteBrand}
                  disabled={deletingBrand}
                >
                  {deletingBrand ? 'Excluindo...' : 'Excluir brand'}
                </button>
              </div>
            </form>
          </div>

          <div className="add-form">
            <h2>Descrição YouTube da marca</h2>
            <p className="form-hint">
              Este texto é anexado automaticamente no final da descrição dos cortes automáticos no YouTube.
            </p>
            <form onSubmit={handleSaveYoutubeDescription}>
              <div className="form-group">
                <label>Conteúdo para crianças (selfDeclaredMadeForKids)</label>
                <select
                  value={youtubeMadeForKids ? 'yes' : 'no'}
                  onChange={(e) => setYoutubeMadeForKids(e.target.value === 'yes')}
                >
                  <option value="no">Não</option>
                  <option value="yes">Sim</option>
                </select>
              </div>
              <div className="form-group">
                <label>Texto adicional padrão (editável por marca)</label>
                <textarea
                  rows={6}
                  value={youtubeDescriptionExtra}
                  onChange={(e) => setYoutubeDescriptionExtra(e.target.value)}
                  placeholder="Ex: Siga nossas redes, links, CTA da marca..."
                />
              </div>
              <button type="submit" disabled={savingYoutubeDescription}>
                {savingYoutubeDescription ? 'Salvando...' : 'Salvar texto da descrição'}
              </button>
            </form>
          </div>

          <div className="add-form">
            <h2>Upload-Post (TikTok, X, Instagram)</h2>
            <p className="form-hint">
              Envia vídeos também para outras redes via Upload-Post.com. Conecte o perfil <code>brand_{brandId}</code> no painel do Upload-Post. Descrição = texto da LLM + texto extra abaixo (se preenchido).
            </p>
            <form onSubmit={handleSaveUploadPost} className="brand-create-form">
              <div className="upload-post-block">
                <div className="form-row" style={{ alignItems: 'center', gap: 12 }}>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={uploadPostTiktokEnabled}
                      onChange={(e) => setUploadPostTiktokEnabled(e.target.checked)}
                    />
                    TikTok
                  </label>
                  <div className="form-group" style={{ flex: 1 }}>
                    <textarea
                      rows={2}
                      value={uploadPostTiktokExtra}
                      onChange={(e) => setUploadPostTiktokExtra(e.target.value)}
                      placeholder="Texto extra na descrição (opcional). Vazio = só o que a LLM retornar."
                      disabled={!uploadPostTiktokEnabled}
                    />
                  </div>
                </div>
              </div>
              <div className="upload-post-block">
                <div className="form-row" style={{ alignItems: 'center', gap: 12 }}>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={uploadPostXEnabled}
                      onChange={(e) => setUploadPostXEnabled(e.target.checked)}
                    />
                    X (Twitter)
                  </label>
                  <div className="form-group" style={{ flex: 1 }}>
                    <textarea
                      rows={2}
                      value={uploadPostXExtra}
                      onChange={(e) => setUploadPostXExtra(e.target.value)}
                      placeholder="Texto extra na descrição (opcional). Vazio = só o que a LLM retornar."
                      disabled={!uploadPostXEnabled}
                    />
                  </div>
                </div>
              </div>
              <div className="upload-post-block">
                <div className="form-row" style={{ alignItems: 'center', gap: 12 }}>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={uploadPostInstagramEnabled}
                      onChange={(e) => setUploadPostInstagramEnabled(e.target.checked)}
                    />
                    Instagram
                  </label>
                  <div className="form-group" style={{ flex: 1 }}>
                    <textarea
                      rows={2}
                      value={uploadPostInstagramExtra}
                      onChange={(e) => setUploadPostInstagramExtra(e.target.value)}
                      placeholder="Texto extra na descrição (opcional). Vazio = só o que a LLM retornar."
                      disabled={!uploadPostInstagramEnabled}
                    />
                  </div>
                </div>
              </div>
              <div className="upload-post-block">
                <div className="form-row" style={{ alignItems: 'center', gap: 12 }}>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={uploadPostYoutubeEnabled}
                      onChange={(e) => setUploadPostYoutubeEnabled(e.target.checked)}
                    />
                    YouTube (via Upload-Post, preferência sobre API direta)
                  </label>
                </div>
              </div>
              <div className="form-actions-row">
                <button type="submit" disabled={savingUploadPost}>
                  {savingUploadPost ? 'Salvando...' : 'Salvar Upload-Post'}
                </button>
              </div>
            </form>
          </div>

          <div className="add-form">
            <h2>YouTube credentials</h2>
            <p className="form-hint">
              Cadastre múltiplas APIs YouTube para esta brand. O sistema tenta em ordem e troca automaticamente em caso de quotaExceeded.
            </p>
            {loadingYoutubeCredentials ? (
              <p>Carregando credenciais...</p>
            ) : youtubeCredentials.length === 0 ? (
              <p className="form-hint">Nenhuma credencial cadastrada ainda.</p>
            ) : (
              <div className="assets-grid">
                {youtubeCredentials.map((cred) => (
                  <div key={cred.id} className="asset-card">
                    <div className="form-group">
                      <label>Rótulo</label>
                      <input
                        type="text"
                        value={cred.label || ''}
                        onChange={(e) => handleYoutubeCredentialFieldChange(cred.id, 'label', e.target.value)}
                        placeholder={`Credencial #${cred.id}`}
                      />
                    </div>
                    <div className="form-group">
                      <label>Ordem</label>
                      <input
                        type="number"
                        min="1"
                        value={cred.order_index || 1}
                        onChange={(e) => handleYoutubeCredentialFieldChange(cred.id, 'order_index', e.target.value)}
                      />
                    </div>
                    <div className="form-group">
                      <label>Google Client ID</label>
                      <input
                        type="text"
                        value={cred.client_id || ''}
                        onChange={(e) => handleYoutubeCredentialFieldChange(cred.id, 'client_id', e.target.value)}
                        placeholder="xxxxx.apps.googleusercontent.com"
                      />
                    </div>
                    <div className="form-group">
                      <label>Google Client Secret</label>
                      {cred.client_secret_configured && (
                        <p className="form-hint">Secret já cadastrado. Preencha apenas para substituir.</p>
                      )}
                      <input
                        type="password"
                        value={youtubeCredentialSecrets[cred.id] || ''}
                        onChange={(e) =>
                          setYoutubeCredentialSecrets((prev) => ({ ...prev, [cred.id]: e.target.value }))
                        }
                        placeholder="Novo secret (opcional)"
                      />
                    </div>
                    <div className="form-group">
                      <label>Redirect URI</label>
                      <input
                        type="url"
                        value={cred.redirect_uri || ''}
                        onChange={(e) => handleYoutubeCredentialFieldChange(cred.id, 'redirect_uri', e.target.value)}
                        placeholder="http://127.0.0.1:8000/social/youtube/callback/"
                      />
                    </div>
                    <div className="form-group">
                      <label>Status</label>
                      <select
                        value={cred.is_active ? '1' : '0'}
                        onChange={(e) => handleYoutubeCredentialFieldChange(cred.id, 'is_active', e.target.value === '1')}
                      >
                        <option value="1">Ativa</option>
                        <option value="0">Inativa</option>
                      </select>
                    </div>
                    <div className="form-hint">
                      {cred.is_connected ? `Conectada: ${cred.account_name || cred.channel_id || '-'}` : 'Sem OAuth conectado'}
                    </div>
                    <div className="form-actions-row">
                      <button
                        type="button"
                        onClick={() => handleSaveYoutubeCredential(cred)}
                        disabled={savingYoutubeCredentialId === cred.id}
                      >
                        {savingYoutubeCredentialId === cred.id ? 'Salvando...' : 'Salvar credencial'}
                      </button>
                      <button
                        type="button"
                        className="danger-btn"
                        onClick={() => handleDeleteYoutubeCredential(cred)}
                        disabled={deletingYoutubeCredentialId === cred.id}
                      >
                        {deletingYoutubeCredentialId === cred.id ? 'Excluindo...' : 'Excluir'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <form onSubmit={handleCreateYoutubeCredential} className="brand-create-form" style={{ marginTop: 12 }}>
              <div className="form-row">
                <div className="form-group">
                  <label>Rótulo</label>
                  <input
                    type="text"
                    value={newYoutubeCredentialLabel}
                    onChange={(e) => setNewYoutubeCredentialLabel(e.target.value)}
                    placeholder="Ex: API principal"
                  />
                </div>
                <div className="form-group">
                  <label>Ordem</label>
                  <input
                    type="number"
                    min="1"
                    value={newYoutubeCredentialOrder}
                    onChange={(e) => setNewYoutubeCredentialOrder(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label>Google Client ID</label>
                  <input
                    type="text"
                    value={newYoutubeCredentialClientId}
                    onChange={(e) => setNewYoutubeCredentialClientId(e.target.value)}
                    placeholder="xxxxx.apps.googleusercontent.com"
                  />
                </div>
                <div className="form-group">
                  <label>Google Client Secret</label>
                  <input
                    type="password"
                    value={newYoutubeCredentialClientSecret}
                    onChange={(e) => setNewYoutubeCredentialClientSecret(e.target.value)}
                    placeholder="GOCSPX-..."
                  />
                </div>
                <div className="form-group">
                  <label>Redirect URI (opcional)</label>
                  <input
                    type="url"
                    value={newYoutubeCredentialRedirectUri}
                    onChange={(e) => setNewYoutubeCredentialRedirectUri(e.target.value)}
                    placeholder="http://127.0.0.1:8000/social/youtube/callback/"
                  />
                </div>
              </div>
              <button type="submit" disabled={creatingYoutubeCredential}>
                {creatingYoutubeCredential ? 'Adicionando...' : '+ Adicionar API'}
              </button>
            </form>
          </div>

          <div className="add-form">
            <h2>Adicionar mídia</h2>
            {assetType === 'LOGO' && (
              <p className="form-hint">
                Ao enviar um novo logo, o sistema remove automaticamente os logos anteriores desta brand.
              </p>
            )}
            {(assetType === 'THUMB_SHORT' || assetType === 'THUMB_LONG') && (
              <p className="form-hint">
                Modelo de capa sobreposto ao frame. PNG com transparência. Dimensões: Thumb Shorts 1080×1920 px, Thumb Longs 1920×1080 px. O título é desenhado por cima na faixa inferior.
              </p>
            )}
            <form onSubmit={handleAdd}>
              <div className="form-row">
                <div className="form-group">
                  <label>Tipo</label>
                  <select value={assetType} onChange={(e) => setAssetType(e.target.value)}>
                    {ASSET_TYPES.map((t) => (
                      <option key={t.id} value={t.id}>{t.label}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>Rótulo (opcional)</label>
                  <input
                    type="text"
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="Ex: Intro 15s"
                  />
                </div>
              </div>
              <div className="form-group">
                <label>Arquivo {(assetType === 'LOGO' || assetType === 'THUMB_SHORT' || assetType === 'THUMB_LONG') ? '(imagem PNG/JPG)' : '(vídeo ou imagem)'}</label>
                <input
                  type="file"
                  accept={(assetType === 'LOGO' || assetType === 'THUMB_SHORT' || assetType === 'THUMB_LONG') ? 'image/*' : 'video/*,image/*'}
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  required
                />
              </div>
              <button type="submit" disabled={uploading}>
                {uploading ? 'Enviando...' : 'Adicionar'}
              </button>
            </form>
          </div>

          <div className="add-form">
            <h2>Adicionar overlay (vídeo longo)</h2>
            <p className="form-hint">
              PNG, JPG ou MP4 para sobrepor ao vídeo longo na lateral direita (canto superior direito se a altura for menor
              que a do vídeo). Vários arquivos: use rótulos diferentes.
            </p>
            <form onSubmit={handleAddOverlay}>
              <div className="form-row">
                <div className="form-group">
                  <label>Rótulo (opcional)</label>
                  <input
                    type="text"
                    value={overlayLongLabel}
                    onChange={(e) => setOverlayLongLabel(e.target.value)}
                    placeholder="Ex: faixa patrocinador"
                  />
                </div>
              </div>
              <div className="form-group">
                <label>Arquivo (PNG, JPG ou MP4)</label>
                <input
                  type="file"
                  accept=".mp4,.png,.jpg,.jpeg,video/mp4,image/png,image/jpeg"
                  onChange={(e) => setOverlayLongFile(e.target.files?.[0] || null)}
                  required
                />
              </div>
              <button type="submit" disabled={uploading}>
                {uploading ? 'Enviando...' : 'Adicionar overlay'}
              </button>
            </form>
          </div>

          <div className="assets-list">
            <h2>Mídias cadastradas</h2>
            {loading ? (
              <p>Carregando...</p>
            ) : assets.length === 0 ? (
              <p className="empty-msg">Nenhuma mídia cadastrada para esta marca.</p>
            ) : (
              <div className="assets-grid">
                {assets.map((asset) => (
                  <div key={asset.id} className="asset-card">
                    <div className="asset-type">{typeLabel(asset.asset_type)}</div>
                    <div className="asset-label">{asset.label || asset.file?.split('/').pop() || '-'}</div>
                    {asset.file && (
                      <a href={asset.file} target="_blank" rel="noreferrer" className="asset-link">
                        Ver arquivo
                      </a>
                    )}
                    <button
                      type="button"
                      className="btn-delete"
                      onClick={() => handleDelete(asset)}
                    >
                      Deletar
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
