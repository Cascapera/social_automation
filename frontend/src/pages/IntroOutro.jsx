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
  const [newBrandShortSlotTimes, setNewBrandShortSlotTimes] = useState([])
  const [newBrandShortSlotTimeInput, setNewBrandShortSlotTimeInput] = useState('10:00')
  const [newBrandLongSlotTimes, setNewBrandLongSlotTimes] = useState([])
  const [newBrandLongSlotTimeInput, setNewBrandLongSlotTimeInput] = useState('20:00')
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
  const [editShortSlotTimes, setEditShortSlotTimes] = useState([])
  const [newShortSlotTime, setNewShortSlotTime] = useState('10:00')
  const [editLongSlotTimes, setEditLongSlotTimes] = useState([])
  const [newLongSlotTime, setNewLongSlotTime] = useState('20:00')
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
    setEditShortSlotTimes(Array.isArray(selected?.short_slot_times) ? selected.short_slot_times : [])
    setEditLongSlotTimes(Array.isArray(selected?.long_slot_times) ? selected.long_slot_times : [])
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
        short_slot_times: newBrandShortSlotTimes?.length ? newBrandShortSlotTimes : [],
        long_slot_times: newBrandLongSlotTimes?.length ? newBrandLongSlotTimes : [],
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
        short_slot_times: Array.isArray(editShortSlotTimes) ? editShortSlotTimes : [],
        long_slot_times: Array.isArray(editLongSlotTimes) ? editLongSlotTimes : [],
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
              <h3>Regras de Agendamento</h3>
              <div className="form-group">
                <label>Horários fixos de short</label>
                <p className="form-hint">Adicione os horários em que os shorts serão postados. Obrigatório.</p>
                <div className="short-slots-list">
                  {(newBrandShortSlotTimes || []).map((t, i) => (
                    <span key={i} className="short-slot-chip">
                      {t}
                      <button type="button" onClick={() => setNewBrandShortSlotTimes((prev) => prev.filter((_, j) => j !== i))} title="Remover">×</button>
                    </span>
                  ))}
                  <div className="short-slot-add">
                    <input type="time" value={newBrandShortSlotTimeInput} onChange={(e) => setNewBrandShortSlotTimeInput(e.target.value)} />
                    <button type="button" onClick={() => {
                      const v = newBrandShortSlotTimeInput?.slice(0, 5)
                      if (v && !newBrandShortSlotTimes.includes(v)) setNewBrandShortSlotTimes((prev) => [...prev, v].sort())
                    }}>Adicionar</button>
                  </div>
                </div>
              </div>
              <div className="form-group">
                <label>Horários fixos de vídeos longos</label>
                <p className="form-hint">Adicione os horários em que os longos serão postados. Obrigatório para agendar longos.</p>
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
              Ajuste os horários fixos de postagem. O número de slots define quantos vídeos serão agendados por dia.
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
              </div>
              <div className="form-group">
                <label>Horários fixos de short</label>
                <p className="form-hint">Adicione os horários em que os shorts serão postados. Obrigatório.</p>
                <div className="short-slots-list">
                  {(editShortSlotTimes || []).map((t, i) => (
                    <span key={i} className="short-slot-chip">
                      {t}
                      <button type="button" onClick={() => setEditShortSlotTimes((prev) => prev.filter((_, j) => j !== i))} title="Remover">×</button>
                    </span>
                  ))}
                  <div className="short-slot-add">
                    <input type="time" value={newShortSlotTime} onChange={(e) => setNewShortSlotTime(e.target.value)} />
                    <button type="button" onClick={() => {
                      const v = newShortSlotTime?.slice(0, 5)
                      if (v && !editShortSlotTimes.includes(v)) setEditShortSlotTimes((prev) => [...prev, v].sort())
                    }}>Adicionar</button>
                  </div>
                </div>
              </div>
              <div className="form-group">
                <label>Horários fixos de vídeos longos</label>
                <p className="form-hint">Adicione os horários em que os longos serão postados. Obrigatório para agendar longos.</p>
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
