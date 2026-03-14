import { useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getSearchChannels,
  createSearchChannel,
  updateSearchChannel,
  deleteSearchChannel,
  getFactory,
  updateFactory,
  getBrands,
  getFactoryYoutubeCheckConnectUrl,
} from '../api'
import './CanaisBusca.css'

export default function CanaisBusca() {
  const { viewMode, factoryId, setFactoryId, setViewMode, brands } = useBrand()
  const [channels, setChannels] = useState([])
  const [factoryInfo, setFactoryInfo] = useState(null)
  const [brandsForFactory, setBrandsForFactory] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [url, setUrl] = useState('')
  const [targetBrandId, setTargetBrandId] = useState('')
  const [isActive, setIsActive] = useState(true)
  const [autoFetchEnabled, setAutoFetchEnabled] = useState(false)
  const [minPerBrand, setMinPerBrand] = useState(3)
  const [minTotal, setMinTotal] = useState(10)
  const [maxTotal, setMaxTotal] = useState(100)
  const [minVideoAgeHours, setMinVideoAgeHours] = useState(24)
  const [maxVideoAgeHours, setMaxVideoAgeHours] = useState(168)
  const [promptVersion, setPromptVersion] = useState('viral')
  const [shortsTarget, setShortsTarget] = useState(12)
  const [longsTarget, setLongsTarget] = useState(3)
  const [minDurationMinutes, setMinDurationMinutes] = useState(50)
  const [minViews, setMinViews] = useState(0)
  const [savingAutoFetch, setSavingAutoFetch] = useState(false)
  const [connectingOAuth, setConnectingOAuth] = useState(false)

  const isFactoryMode = viewMode === 'factory' && factoryId

  // Ao retornar do OAuth: ler factory_id da URL e atualizar contexto para garantir que o status "OAuth conectado" apareça
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const err = params.get('error')
    if (err) setError(decodeURIComponent(err))
    const urlFactoryId = params.get('factory_id')
    if (urlFactoryId) {
      setFactoryId(urlFactoryId)
      setViewMode('factory')
    }
    const connected = params.get('youtube_check_connected')
    if (err || urlFactoryId || connected) {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [setFactoryId, setViewMode])

  useEffect(() => {
    if (isFactoryMode && factoryId) {
      loadData()
    } else {
      setChannels([])
      setFactoryInfo(null)
      setBrandsForFactory([])
      setLoading(false)
    }
  }, [viewMode, factoryId])

  async function loadData() {
    if (!factoryId) return
    setLoading(true)
    setError('')
    try {
      const [chList, factory, brandList] = await Promise.all([
        getSearchChannels(factoryId),
        getFactory(factoryId),
        getBrands(factoryId),
      ])
      setChannels(Array.isArray(chList) ? chList : (chList?.results || []))
      setFactoryInfo(factory)
      setBrandsForFactory(Array.isArray(brandList) ? brandList : [])
      setAutoFetchEnabled(factory?.auto_fetch_enabled ?? false)
      setMinPerBrand(factory?.auto_fetch_min_per_brand ?? 3)
      setMinTotal(factory?.auto_fetch_min_total ?? 10)
      setMaxTotal(factory?.auto_fetch_max_total ?? 100)
      setMinVideoAgeHours(factory?.auto_fetch_min_video_age_hours ?? 24)
      setMaxVideoAgeHours(factory?.auto_fetch_max_video_age_hours ?? 168)
      setPromptVersion(factory?.auto_fetch_prompt_version ?? 'viral')
      setShortsTarget(factory?.auto_fetch_shorts_target ?? 12)
      setLongsTarget(factory?.auto_fetch_longs_target ?? 3)
      setMinDurationMinutes(factory?.auto_fetch_min_duration_minutes ?? 50)
      setMinViews(factory?.auto_fetch_min_views ?? 0)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function openForm(ch = null) {
    if (ch) {
      setEditingId(ch.id)
      setUrl(ch.youtube_channel_url || '')
      setTargetBrandId(ch.distribute_by_brands ? 'distribute' : (ch.target_brand_id || ''))
      setIsActive(ch.is_active ?? true)
    } else {
      setEditingId(null)
      setUrl('')
      setTargetBrandId('')
      setIsActive(true)
    }
    setFormOpen(true)
  }

  function closeForm() {
    setFormOpen(false)
    setEditingId(null)
    setUrl('')
    setTargetBrandId('')
    setError('')
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!url.trim()) {
      setError('Informe a URL do canal')
      return
    }
    if (!factoryId) return
    setSaving(true)
    setError('')
    try {
      const isDistribute = targetBrandId === 'distribute'
      const payload = {
        factory: factoryId,
        youtube_channel_url: url.trim(),
        target_brand: isDistribute ? null : (targetBrandId || null),
        distribute_by_brands: isDistribute,
        is_active: isActive,
      }
      if (editingId) {
        await updateSearchChannel(editingId, payload)
      } else {
        await createSearchChannel(payload)
      }
      closeForm()
      loadData()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id) {
    if (!confirm('Remover este canal de busca?')) return
    try {
      await deleteSearchChannel(id)
      loadData()
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleConnectOAuth() {
    if (!factoryId) return
    setConnectingOAuth(true)
    setError('')
    try {
      const url = await getFactoryYoutubeCheckConnectUrl(factoryId)
      window.location.href = url
    } catch (e) {
      setError(e.message)
      setConnectingOAuth(false)
    }
  }

  async function handleSaveAutoFetch() {
    if (!factoryId) return
    setSavingAutoFetch(true)
    setError('')
    try {
      await updateFactory(factoryId, {
        auto_fetch_enabled: autoFetchEnabled,
        auto_fetch_min_per_brand: minPerBrand,
        auto_fetch_min_total: minTotal,
        auto_fetch_max_total: maxTotal,
        auto_fetch_min_video_age_hours: minVideoAgeHours,
        auto_fetch_max_video_age_hours: maxVideoAgeHours,
        auto_fetch_min_duration_minutes: minDurationMinutes,
        auto_fetch_min_views: minViews,
        auto_fetch_prompt_version: promptVersion,
        auto_fetch_shorts_target: shortsTarget,
        auto_fetch_longs_target: longsTarget,
      })
      setFactoryInfo((prev) => ({
        ...prev,
        auto_fetch_enabled: autoFetchEnabled,
        auto_fetch_min_per_brand: minPerBrand,
        auto_fetch_min_total: minTotal,
        auto_fetch_max_total: maxTotal,
        auto_fetch_min_video_age_hours: minVideoAgeHours,
        auto_fetch_max_video_age_hours: maxVideoAgeHours,
        auto_fetch_prompt_version: promptVersion,
        auto_fetch_shorts_target: shortsTarget,
        auto_fetch_longs_target: longsTarget,
        auto_fetch_min_duration_minutes: minDurationMinutes,
        auto_fetch_min_views: minViews,
      }))
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingAutoFetch(false)
    }
  }

  if (!isFactoryMode) {
    return (
      <div className="canais-busca-page">
        <div className="canais-busca-empty">
          <p>Selecione uma Factory no menu à esquerda para gerenciar os Canais de Busca.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="canais-busca-page">
      <h1>Canais de Busca</h1>
      <p className="canais-busca-desc">
        Canais do YouTube que esta factory monitora para buscar vídeos automaticamente.
        Ao cadastrar, direcione para uma Brand, use &quot;Por tema&quot; (IA) ou &quot;Distribuir pelas Brands&quot; para equilibrar o estoque.
      </p>

      {error && (
        <div className="canais-busca-error">
          {error}
        </div>
      )}

      <section className="canais-busca-oauth">
        <h2>API YouTube para busca</h2>
        <p className="oauth-desc">
          Para buscar vídeos nos canais, use YOUTUBE_API_KEY no .env ou conecte uma conta OAuth (YOUTUBE_CHECK_*).
        </p>
        {factoryInfo?.has_youtube_check_credential ? (
          <p className="oauth-status connected">OAuth conectado para esta factory.</p>
        ) : (
          <button
            type="button"
            className="btn-connect"
            onClick={handleConnectOAuth}
            disabled={connectingOAuth}
          >
            {connectingOAuth ? 'Redirecionando...' : 'Conectar OAuth (YOUTUBE_CHECK_*)'}
          </button>
        )}
      </section>

      <section className="canais-busca-auto-fetch">
        <h2>Buscar vídeos automaticamente</h2>
        <div className="auto-fetch-form">
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={autoFetchEnabled}
              onChange={(e) => setAutoFetchEnabled(e.target.checked)}
            />
            <span className="toggle-slider" />
            Ativar busca automática
          </label>
          <div className="auto-fetch-fields">
            <div className="form-group">
              <label>Mín. vídeos por brand</label>
              <input
                type="number"
                min={1}
                max={50}
                value={minPerBrand}
                onChange={(e) => setMinPerBrand(Number(e.target.value) || 3)}
              />
            </div>
            <div className="form-group">
              <label>Mín. total na factory</label>
              <input
                type="number"
                min={1}
                max={500}
                value={minTotal}
                onChange={(e) => setMinTotal(Number(e.target.value) || 10)}
              />
            </div>
            <div className="form-group">
              <label>Máx. total no banco</label>
              <input
                type="number"
                min={10}
                max={1000}
                value={maxTotal}
                onChange={(e) => setMaxTotal(Number(e.target.value) || 100)}
              />
            </div>
            <div className="form-group">
              <label>Mín. duração do vídeo (min)</label>
              <input
                type="number"
                min={10}
                max={300}
                value={minDurationMinutes}
                onChange={(e) => setMinDurationMinutes(Number(e.target.value) || 50)}
                title="Evita cortes/shorts que os canais postam; 50 min = vídeos longos"
              />
              <small className="form-hint">Ex: 50 min evita cortes próprios dos canais</small>
            </div>
            <div className="form-group">
              <label>Mín. visualizações</label>
              <input
                type="number"
                min={0}
                max={100000000}
                value={minViews}
                onChange={(e) => setMinViews(Number(e.target.value) || 0)}
                title="0 = sem filtro. Ex: 10000 para só vídeos com 10k+ views"
              />
              <small className="form-hint">0 = sem filtro. Ex: 10000 para vídeos com 10k+ views</small>
            </div>
            <div className="form-group">
              <label>Mín. horas desde publicação</label>
              <input
                type="number"
                min={12}
                max={168}
                value={minVideoAgeHours}
                onChange={(e) => setMinVideoAgeHours(Number(e.target.value) || 24)}
                title="Política de direitos: maioria dos canais exige 24h"
              />
            </div>
            <div className="form-group">
              <label>Máx. horas desde publicação</label>
              <input
                type="number"
                min={24}
                max={720}
                value={maxVideoAgeHours}
                onChange={(e) => setMaxVideoAgeHours(Number(e.target.value) || 168)}
                title="Tema esfria após 1 semana; 168h = 7 dias"
              />
            </div>
            <div className="form-group">
              <label>Modo de análise</label>
              <select
                value={promptVersion}
                onChange={(e) => setPromptVersion(e.target.value)}
                title="Usado em todos os jobs criados pela busca automática"
              >
                <option value="viral">Viral (PT)</option>
                <option value="educational">Educacional (PT)</option>
                <option value="viral_en">Viral (EN)</option>
                <option value="educational_en">Educacional (EN)</option>
                <option value="viral_translate">Viral Translate (EN→PT)</option>
              </select>
              <small className="form-hint">Aplica a todas as brands da factory</small>
            </div>
            <div className="form-group">
              <label>Shorts por job</label>
              <input
                type="number"
                min={1}
                max={30}
                value={shortsTarget}
                onChange={(e) => setShortsTarget(Number(e.target.value) || 12)}
                title="Quantidade de cortes curtos por job"
              />
            </div>
            <div className="form-group">
              <label>Longos por job</label>
              <input
                type="number"
                min={1}
                max={10}
                value={longsTarget}
                onChange={(e) => setLongsTarget(Number(e.target.value) || 3)}
                title="Quantidade de cortes longos por job"
              />
            </div>
          </div>
          <button
            type="button"
            className="btn-save"
            onClick={handleSaveAutoFetch}
            disabled={savingAutoFetch}
          >
            {savingAutoFetch ? 'Salvando...' : 'Salvar configurações'}
          </button>
        </div>
      </section>

      <section className="canais-busca-list">
        <div className="section-header">
          <h2>Canais cadastrados</h2>
          <button type="button" className="btn-add" onClick={() => openForm()}>
            + Adicionar canal
          </button>
        </div>

        {loading ? (
          <p>Carregando...</p>
        ) : channels.length === 0 ? (
          <p className="canais-busca-empty-list">Nenhum canal cadastrado. Adicione URLs de canais do YouTube (ex: Flow Podcast, Inteligência Limitada).</p>
        ) : (
          <table className="canais-table">
            <thead>
              <tr>
                <th>Canal</th>
                <th>Direcionar para</th>
                <th>Status</th>
                <th>Ações</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((ch) => (
                <tr key={ch.id}>
                  <td>
                    <div className="channel-info">
                      <strong>{ch.channel_title || ch.youtube_channel_id || '—'}</strong>
                      <small>{ch.youtube_channel_url}</small>
                    </div>
                  </td>
                  <td>{ch.target_brand_name || 'Todos'}</td>
                  <td>
                    <span className={`status-badge ${ch.is_active ? 'active' : 'inactive'}`}>
                      {ch.is_active ? 'Ativo' : 'Inativo'}
                    </span>
                  </td>
                  <td>
                    <button type="button" className="btn-edit" onClick={() => openForm(ch)}>Editar</button>
                    <button type="button" className="btn-delete" onClick={() => handleDelete(ch.id)}>Remover</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {formOpen && (
        <div className="canais-modal-overlay" onClick={closeForm}>
          <div className="canais-modal" onClick={(e) => e.stopPropagation()}>
            <h3>{editingId ? 'Editar canal' : 'Adicionar canal'}</h3>
            <form onSubmit={handleSubmit}>
              <div className="form-group">
                <label>URL do canal YouTube</label>
                <input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://youtube.com/@flowpodcast ou youtube.com/channel/UC..."
                />
              </div>
              <div className="form-group">
                <label>Direcionar para</label>
                <select
                  value={targetBrandId}
                  onChange={(e) => setTargetBrandId(e.target.value || '')}
                >
                  <option value="">Por tema (IA)</option>
                  <option value="distribute">Distribuir pelas Brands</option>
                  {brandsForFactory.map((b) => (
                    <option key={b.id} value={b.id}>Direcionar para {b.name}</option>
                  ))}
                </select>
                <span className="form-hint">Por tema: usa categoria da IA. Distribuir: envia para a brand com menos vídeos no banco.</span>
              </div>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                Canal ativo para busca
              </label>
              <div className="modal-actions">
                <button type="button" onClick={closeForm}>Cancelar</button>
                <button type="submit" disabled={saving}>
                  {saving ? 'Salvando...' : (editingId ? 'Salvar' : 'Adicionar')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
