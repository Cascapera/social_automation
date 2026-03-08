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
} from '../api'
import './IntroOutro.css'

const ASSET_TYPES = [
  { id: 'LOGO', label: 'Logo' },
  { id: 'INTRO', label: 'Intro' },
  { id: 'OUTRO', label: 'Outro' },
  { id: 'CTA', label: 'CTA' },
  { id: 'ANIMATION', label: 'Animação overlay (PNG/GIF)' },
]

const THEME_CATEGORY_OPTIONS = [
  { id: 'BUSINESS_MONEY', label: 'Negócios / Dinheiro' },
  { id: 'PSYCHOLOGY_RELATIONSHIPS', label: 'Psicologia / Relacionamentos' },
  { id: 'STORIES_CURIOSITIES', label: 'Histórias e Curiosidades' },
  { id: 'CONTROVERSIES_DEBATE', label: 'Polêmicas / Debate' },
  { id: 'COMEDY_HUMOR', label: 'Comédia / Humor' },
]

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
  const [newBrandYoutubeClientId, setNewBrandYoutubeClientId] = useState('')
  const [newBrandYoutubeClientSecret, setNewBrandYoutubeClientSecret] = useState('')
  const [newBrandYoutubeRedirectUri, setNewBrandYoutubeRedirectUri] = useState('')
  const [newBrandShortMinInterval, setNewBrandShortMinInterval] = useState(60)
  const [newBrandLongMinInterval, setNewBrandLongMinInterval] = useState(180)
  const [newBrandMaxShortsPerDay, setNewBrandMaxShortsPerDay] = useState(3)
  const [newBrandMaxLongsPerDay, setNewBrandMaxLongsPerDay] = useState(1)
  const [newBrandShortWindowStart, setNewBrandShortWindowStart] = useState('08:00')
  const [newBrandShortWindowEnd, setNewBrandShortWindowEnd] = useState('22:00')
  const [newBrandLongWindowStart, setNewBrandLongWindowStart] = useState('12:00')
  const [newBrandLongWindowEnd, setNewBrandLongWindowEnd] = useState('22:00')
  const [creatingBrand, setCreatingBrand] = useState(false)
  const [showNewBrand, setShowNewBrand] = useState(false)
  const [youtubeDescriptionExtra, setYoutubeDescriptionExtra] = useState('')
  const [youtubeMadeForKids, setYoutubeMadeForKids] = useState(false)
  const [savingYoutubeDescription, setSavingYoutubeDescription] = useState(false)
  const [editingBrand, setEditingBrand] = useState(false)
  const [deletingBrand, setDeletingBrand] = useState(false)
  const [editThemeCategory, setEditThemeCategory] = useState('')
  const [editThumbnailFont, setEditThumbnailFont] = useState('impact')
  const [editBandColor, setEditBandColor] = useState('#E12E20')
  const [editTextColor, setEditTextColor] = useState('#0A0A0A')
  const [editEffectColor, setEditEffectColor] = useState('#FFEBDC')
  const [editShortMinInterval, setEditShortMinInterval] = useState(60)
  const [editLongMinInterval, setEditLongMinInterval] = useState(180)
  const [editMaxShortsPerDay, setEditMaxShortsPerDay] = useState(3)
  const [editMaxLongsPerDay, setEditMaxLongsPerDay] = useState(1)
  const [editShortWindowStart, setEditShortWindowStart] = useState('08:00')
  const [editShortWindowEnd, setEditShortWindowEnd] = useState('22:00')
  const [editLongWindowStart, setEditLongWindowStart] = useState('12:00')
  const [editLongWindowEnd, setEditLongWindowEnd] = useState('22:00')
  const [editYoutubeClientId, setEditYoutubeClientId] = useState('')
  const [editYoutubeClientSecret, setEditYoutubeClientSecret] = useState('')
  const [editYoutubeRedirectUri, setEditYoutubeRedirectUri] = useState('')
  const [editYoutubeSecretConfigured, setEditYoutubeSecretConfigured] = useState(false)
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
    setEditThumbnailFont(selected?.thumbnail_font || 'impact')
    setEditBandColor(selected?.thumbnail_band_color || '#E12E20')
    setEditTextColor(selected?.thumbnail_text_color || '#0A0A0A')
    setEditEffectColor(selected?.thumbnail_effect_color || '#FFEBDC')
    setEditShortMinInterval(Number(selected?.min_short_interval_minutes ?? 60))
    setEditLongMinInterval(Number(selected?.min_long_interval_minutes ?? 180))
    setEditMaxShortsPerDay(Number(selected?.max_shorts_per_day ?? 3))
    setEditMaxLongsPerDay(Number(selected?.max_longs_per_day ?? 1))
    setEditShortWindowStart(selected?.short_window_start || '08:00')
    setEditShortWindowEnd(selected?.short_window_end || '22:00')
    setEditLongWindowStart(selected?.long_window_start || '12:00')
    setEditLongWindowEnd(selected?.long_window_end || '22:00')
    setEditYoutubeClientId(selected?.youtube_client_id || '')
    setEditYoutubeClientSecret('')
    setEditYoutubeRedirectUri(selected?.youtube_redirect_uri || '')
    setEditYoutubeSecretConfigured(!!selected?.youtube_client_secret_configured)
  }, [brandId, brands])

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
        youtube_client_id: newBrandYoutubeClientId || '',
        youtube_redirect_uri: newBrandYoutubeRedirectUri || '',
        min_short_interval_minutes: Number(newBrandShortMinInterval || 0),
        min_long_interval_minutes: Number(newBrandLongMinInterval || 0),
        max_shorts_per_day: Number(newBrandMaxShortsPerDay || 0),
        max_longs_per_day: Number(newBrandMaxLongsPerDay || 0),
        short_window_start: newBrandShortWindowStart || null,
        short_window_end: newBrandShortWindowEnd || null,
        long_window_start: newBrandLongWindowStart || null,
        long_window_end: newBrandLongWindowEnd || null,
      }
      if ((newBrandYoutubeClientSecret || '').trim()) {
        payload.youtube_client_secret = newBrandYoutubeClientSecret
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
      setNewBrandYoutubeClientId('')
      setNewBrandYoutubeClientSecret('')
      setNewBrandYoutubeRedirectUri('')
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
    setEditingBrand(true)
    setError('')
    try {
      await updateBrand(brandId, {
        theme_category: editThemeCategory || '',
        thumbnail_font: editThumbnailFont || 'impact',
        thumbnail_band_color: (editBandColor || '').trim(),
        thumbnail_text_color: (editTextColor || '').trim(),
        thumbnail_effect_color: (editEffectColor || '').trim(),
        min_short_interval_minutes: Number(editShortMinInterval || 0),
        min_long_interval_minutes: Number(editLongMinInterval || 0),
        max_shorts_per_day: Number(editMaxShortsPerDay || 0),
        max_longs_per_day: Number(editMaxLongsPerDay || 0),
        short_window_start: editShortWindowStart || null,
        short_window_end: editShortWindowEnd || null,
        long_window_start: editLongWindowStart || null,
        long_window_end: editLongWindowEnd || null,
        youtube_client_id: editYoutubeClientId || '',
        youtube_redirect_uri: editYoutubeRedirectUri || '',
        ...(((editYoutubeClientSecret || '').trim())
          ? { youtube_client_secret: editYoutubeClientSecret }
          : {}),
      })
      setEditYoutubeClientSecret('')
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

  const typeLabel = (id) => ASSET_TYPES.find((t) => t.id === id)?.label || id

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
                    {THEME_CATEGORY_OPTIONS.map((t) => (
                      <option key={t.id} value={t.id}>{t.label}</option>
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
              <h3>OAuth YouTube (opcional por brand)</h3>
              <p className="form-hint">
                Preencha para usar um projeto Google Cloud exclusivo neste canal. Se vazio, usa o fallback global do .env.
              </p>
              <div className="form-row">
                <div className="form-group">
                  <label>Google Client ID</label>
                  <input
                    type="text"
                    value={newBrandYoutubeClientId}
                    onChange={(e) => setNewBrandYoutubeClientId(e.target.value)}
                    placeholder="xxxxx.apps.googleusercontent.com"
                  />
                </div>
                <div className="form-group">
                  <label>Google Client Secret</label>
                  <input
                    type="password"
                    value={newBrandYoutubeClientSecret}
                    onChange={(e) => setNewBrandYoutubeClientSecret(e.target.value)}
                    placeholder="GOCSPX-..."
                  />
                </div>
                <div className="form-group">
                  <label>YouTube Redirect URI</label>
                  <input
                    type="url"
                    value={newBrandYoutubeRedirectUri}
                    onChange={(e) => setNewBrandYoutubeRedirectUri(e.target.value)}
                    placeholder="http://localhost:8000/api/youtube/callback/"
                  />
                </div>
              </div>
            </div>

            <div className="form-section">
              <h3>Regras de Agendamento</h3>
              <div className="form-row">
                <div className="form-group">
                  <label>Intervalo mínimo short (min)</label>
                  <input type="number" min="0" value={newBrandShortMinInterval} onChange={(e) => setNewBrandShortMinInterval(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Intervalo mínimo longo (min)</label>
                  <input type="number" min="0" value={newBrandLongMinInterval} onChange={(e) => setNewBrandLongMinInterval(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Máx shorts por dia</label>
                  <input type="number" min="0" value={newBrandMaxShortsPerDay} onChange={(e) => setNewBrandMaxShortsPerDay(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Máx longos por dia</label>
                  <input type="number" min="0" value={newBrandMaxLongsPerDay} onChange={(e) => setNewBrandMaxLongsPerDay(e.target.value)} />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Janela shorts (início/fim)</label>
                  <div className="form-row form-row-inline">
                    <input type="time" value={newBrandShortWindowStart} onChange={(e) => setNewBrandShortWindowStart(e.target.value)} />
                    <input type="time" value={newBrandShortWindowEnd} onChange={(e) => setNewBrandShortWindowEnd(e.target.value)} />
                  </div>
                </div>
                <div className="form-group">
                  <label>Janela longos (início/fim)</label>
                  <div className="form-row form-row-inline">
                    <input type="time" value={newBrandLongWindowStart} onChange={(e) => setNewBrandLongWindowStart(e.target.value)} />
                    <input type="time" value={newBrandLongWindowEnd} onChange={(e) => setNewBrandLongWindowEnd(e.target.value)} />
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
              Ajuste horários, intervalos e volume de postagem para esta brand.
            </p>
            <form onSubmit={handleSaveBrandScheduling} className="brand-create-form">
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
                    {THEME_CATEGORY_OPTIONS.map((t) => (
                      <option key={t.id} value={t.id}>{t.label}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>Intervalo mínimo short (min)</label>
                  <input type="number" min="0" value={editShortMinInterval} onChange={(e) => setEditShortMinInterval(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Intervalo mínimo longo (min)</label>
                  <input type="number" min="0" value={editLongMinInterval} onChange={(e) => setEditLongMinInterval(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Máx shorts por dia</label>
                  <input type="number" min="0" value={editMaxShortsPerDay} onChange={(e) => setEditMaxShortsPerDay(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Máx longos por dia</label>
                  <input type="number" min="0" value={editMaxLongsPerDay} onChange={(e) => setEditMaxLongsPerDay(e.target.value)} />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Google Client ID (opcional por brand)</label>
                  <input
                    type="text"
                    value={editYoutubeClientId}
                    onChange={(e) => setEditYoutubeClientId(e.target.value)}
                    placeholder="xxxxx.apps.googleusercontent.com"
                  />
                </div>
                <div className="form-group">
                  <label>Google Client Secret (opcional por brand)</label>
                  {editYoutubeSecretConfigured && (
                    <p className="form-hint">Secret já cadastrado. Preencha apenas para substituir.</p>
                  )}
                  <input
                    type="password"
                    value={editYoutubeClientSecret}
                    onChange={(e) => setEditYoutubeClientSecret(e.target.value)}
                    placeholder="GOCSPX-..."
                  />
                </div>
                <div className="form-group">
                  <label>YouTube Redirect URI (opcional por brand)</label>
                  <input
                    type="url"
                    value={editYoutubeRedirectUri}
                    onChange={(e) => setEditYoutubeRedirectUri(e.target.value)}
                    placeholder="http://localhost:8000/api/youtube/callback/"
                  />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Janela shorts (início/fim)</label>
                  <div className="form-row form-row-inline">
                    <input type="time" value={editShortWindowStart} onChange={(e) => setEditShortWindowStart(e.target.value)} />
                    <input type="time" value={editShortWindowEnd} onChange={(e) => setEditShortWindowEnd(e.target.value)} />
                  </div>
                </div>
                <div className="form-group">
                  <label>Janela longos (início/fim)</label>
                  <div className="form-row form-row-inline">
                    <input type="time" value={editLongWindowStart} onChange={(e) => setEditLongWindowStart(e.target.value)} />
                    <input type="time" value={editLongWindowEnd} onChange={(e) => setEditLongWindowEnd(e.target.value)} />
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
                <label>Arquivo {assetType === 'LOGO' ? '(imagem PNG/JPG)' : '(vídeo ou imagem)'}</label>
                <input
                  type="file"
                  accept={assetType === 'LOGO' ? 'image/*' : 'video/*,image/*'}
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  required
                />
              </div>
              <button type="submit" disabled={uploading}>
                {uploading ? 'Enviando...' : 'Adicionar'}
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
