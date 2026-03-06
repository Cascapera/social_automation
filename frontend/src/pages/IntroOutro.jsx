import { useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getBrands,
  getBrandAssets,
  createBrandAsset,
  deleteBrandAsset,
  createBrand,
  updateBrandYoutubeDescription,
} from '../api'
import './IntroOutro.css'

const ASSET_TYPES = [
  { id: 'LOGO', label: 'Logo' },
  { id: 'INTRO', label: 'Intro' },
  { id: 'OUTRO', label: 'Outro' },
  { id: 'CTA', label: 'CTA' },
  { id: 'ANIMATION', label: 'Animação overlay (PNG/GIF)' },
]

export default function IntroOutro() {
  const { brandId, setBrandId, brands, refreshBrands } = useBrand()
  const [assets, setAssets] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Form para adicionar
  const [assetType, setAssetType] = useState('INTRO')
  const [label, setLabel] = useState('')
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [newBrandName, setNewBrandName] = useState('')
  const [creatingBrand, setCreatingBrand] = useState(false)
  const [showNewBrand, setShowNewBrand] = useState(false)
  const [youtubeDescriptionExtra, setYoutubeDescriptionExtra] = useState('')
  const [youtubeMadeForKids, setYoutubeMadeForKids] = useState(false)
  const [savingYoutubeDescription, setSavingYoutubeDescription] = useState(false)

  useEffect(() => {
    refreshBrands(getBrands)
  }, [])

  useEffect(() => {
    if (brandId) {
      setLoading(true)
      getBrandAssets(brandId)
        .then(setAssets)
        .catch(() => setAssets([]))
        .finally(() => setLoading(false))
    } else {
      setAssets([])
      setLoading(false)
    }
  }, [brandId])

  useEffect(() => {
    const selected = brands.find((b) => String(b.id) === String(brandId))
    setYoutubeDescriptionExtra(selected?.youtube_description_extra || '')
    setYoutubeMadeForKids(!!selected?.youtube_made_for_kids)
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
    setError('')
    setCreatingBrand(true)
    try {
      const b = await createBrand(newBrandName.trim())
      await refreshBrands(getBrands)
      setBrandId(String(b.id))
      setNewBrandName('')
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
      await refreshBrands(getBrands)
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingYoutubeDescription(false)
    }
  }

  const typeLabel = (id) => ASSET_TYPES.find((t) => t.id === id)?.label || id

  return (
    <div className="intro-outro">
      <h1>Mídias da marca</h1>
      <p className="page-desc">
        Adicione logo, vídeos de intro, outro e CTA. O logo é usado automaticamente nos cortes automáticos
        quando a opção &quot;Enquadrar e centralizar&quot; for selecionada (se não houver logo, apenas o fundo colorido).
      </p>

      {!brandId ? (
        <p className="form-hint">
          Selecione uma marca no menu à esquerda.
          {brands.length === 0 && ' Nenhuma marca cadastrada. '}
          {!showNewBrand ? (
            <button type="button" className="btn-link" onClick={() => setShowNewBrand(true)}>+ Nova marca</button>
          ) : (
            <form onSubmit={handleCreateBrand} className="new-brand-form" style={{ display: 'inline-flex', marginTop: '0.5rem' }}>
              <input type="text" value={newBrandName} onChange={(e) => setNewBrandName(e.target.value)} placeholder="Nome da marca" autoFocus />
              <button type="submit" disabled={creatingBrand}>{creatingBrand ? 'Criando...' : 'Criar'}</button>
              <button type="button" onClick={() => { setShowNewBrand(false); setNewBrandName('') }}>Cancelar</button>
            </form>
          )}
        </p>
      ) : (
        <p className="form-hint">Marca: {brands.find((b) => String(b.id) === brandId)?.name || brandId}</p>
      )}

      {error && <div className="form-error">{error}</div>}

      {brandId && (
        <>
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
            <h2>Adicionar mídia</h2>
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
