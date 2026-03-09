import { useEffect, useMemo, useState } from 'react'
import { useBrand } from '../context/BrandContext'
import { getVideoInventory, removeAwaitingInventoryItem, retryAwaitingInventoryItem } from '../api'
import './BancoVideos.css'

const STATUS_LABEL = {
  AVAILABLE: 'Disponível',
  SCHEDULED: 'Agendado',
  POSTED: 'Postado',
  FAILED: 'Falhou',
}

const TYPE_LABEL = {
  SHORT: 'Short',
  LONG: 'Longo',
}

function formatDate(value) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '-'
  return d.toLocaleString('pt-BR')
}

export default function BancoVideos() {
  const { viewMode, factoryId, brandId, brands } = useBrand()
  const [items, setItems] = useState([])
  const [videoType, setVideoType] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [removingId, setRemovingId] = useState(null)
  const [retryingId, setRetryingId] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      const effectiveFactoryId = viewMode === 'factory' ? factoryId : null
      const effectiveBrandId = brandId || null

      if (!effectiveFactoryId && !effectiveBrandId) {
        setItems([])
        return
      }

      setLoading(true)
      setError('')
      try {
        const rows = await getVideoInventory({
          factoryId: effectiveFactoryId,
          brandId: effectiveBrandId,
          videoType: videoType || null,
        })
        if (!cancelled) setItems(Array.isArray(rows) ? rows : [])
      } catch (e) {
        if (!cancelled) {
          setItems([])
          setError(e.message || 'Erro ao carregar banco de vídeos.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [viewMode, factoryId, brandId, videoType])

  const brandNameById = useMemo(
    () => Object.fromEntries((brands || []).map((b) => [String(b.id), b.name])),
    [brands],
  )

  const summary = useMemo(() => {
    const acc = { total: items.length, AVAILABLE: 0, SCHEDULED: 0, POSTED: 0, FAILED: 0 }
    items.forEach((item) => {
      if (acc[item.status] != null) acc[item.status] += 1
    })
    return acc
  }, [items])

  const awaitingItems = useMemo(
    () => items.filter((item) => item.status !== 'POSTED'),
    [items],
  )
  const postedItems = useMemo(
    () => items.filter((item) => item.status === 'POSTED'),
    [items],
  )

  const needsContext = !factoryId && !brandId

  async function handleRemoveAwaiting(item) {
    if (!item?.id) return
    if (!confirm('Excluir este vídeo aguardando postagem do banco?')) return
    setError('')
    setRemovingId(item.id)
    try {
      await removeAwaitingInventoryItem(item.id)
      setItems((prev) => prev.filter((x) => x.id !== item.id))
    } catch (e) {
      setError(e.message || 'Erro ao excluir vídeo do banco.')
    } finally {
      setRemovingId(null)
    }
  }

  async function handleRetryPosting(item) {
    if (!item?.id) return
    setError('')
    setRetryingId(item.id)
    try {
      const result = await retryAwaitingInventoryItem(item.id)
      setItems((prev) =>
        prev.map((x) =>
          x.id === item.id
            ? {
                ...x,
                status: 'SCHEDULED',
                scheduled_for: result?.scheduled_for || x.scheduled_for,
                last_error: '',
              }
            : x,
        ),
      )
    } catch (e) {
      setError(e.message || 'Erro ao tentar novamente a postagem.')
    } finally {
      setRetryingId(null)
    }
  }

  return (
    <div className="banco-videos">
      <h1>Banco de Vídeos</h1>
      <p className="page-desc">
        Inventário de cortes finalizados prontos para agendamento e publicação automática.
      </p>

      {needsContext && (
        <div className="form-error">
          Selecione uma Factory ou uma Brand no menu lateral para visualizar o inventário.
        </div>
      )}

      {error && <div className="form-error">{error}</div>}

      <section className="section banco-filters">
        <div className="banco-summary">
          <span>Total: {summary.total}</span>
          <span>Aguardando: {awaitingItems.length}</span>
          <span>Postados: {postedItems.length}</span>
          <span>Disponíveis: {summary.AVAILABLE}</span>
          <span>Agendados: {summary.SCHEDULED}</span>
          <span>Falhas: {summary.FAILED}</span>
        </div>
        <div className="banco-filter-row">
          <label>
            Tipo
            <select value={videoType} onChange={(e) => setVideoType(e.target.value)}>
              <option value="">Todos</option>
              <option value="SHORT">Short</option>
              <option value="LONG">Longo</option>
            </select>
          </label>
        </div>
      </section>

      <section className="section">
        {loading ? (
          <p className="empty-msg">Carregando inventário...</p>
        ) : items.length === 0 ? (
          <p className="empty-msg">Nenhum vídeo encontrado para os filtros selecionados.</p>
        ) : (
          <div className="banco-sections">
            <div className="banco-block">
              <h2>Aguardando Postagem</h2>
              {awaitingItems.length === 0 ? (
                <p className="empty-msg">Não há vídeos aguardando postagem.</p>
              ) : (
                <div className="banco-table-wrap">
                  <table className="banco-table">
                    <thead>
                      <tr>
                        <th>Brand</th>
                        <th>Tipo</th>
                        <th>Título</th>
                        <th>Score</th>
                        <th>Fonte</th>
                        <th>Status</th>
                        <th>Criado</th>
                        <th>Agendado</th>
                        <th>Erro</th>
                        <th>Ações</th>
                      </tr>
                    </thead>
                    <tbody>
                      {awaitingItems.map((item) => (
                        <tr key={item.id}>
                          <td>{brandNameById[String(item.brand)] || `Brand #${item.brand}`}</td>
                          <td>{TYPE_LABEL[item.video_type] || item.video_type || '-'}</td>
                          <td className="banco-title">{item.title || '-'}</td>
                          <td>{item.virality_score ?? '-'}</td>
                          <td>{item.source_display_name || item.source_asset_id || '-'}</td>
                          <td>{STATUS_LABEL[item.status] || item.status || '-'}</td>
                          <td>{formatDate(item.created_at)}</td>
                          <td>{formatDate(item.scheduled_for)}</td>
                          <td className="banco-error">{item.last_error || '-'}</td>
                          <td>
                            <button
                              type="button"
                              className="btn-action"
                              onClick={() => handleRetryPosting(item)}
                              disabled={retryingId === item.id || item.status === 'POSTING'}
                              title="Tentar novamente a postagem deste vídeo"
                              style={{ marginRight: 8 }}
                            >
                              {retryingId === item.id ? 'Tentando...' : 'Tentar novamente'}
                            </button>
                            <button
                              type="button"
                              className="btn-action btn-cancel"
                              onClick={() => handleRemoveAwaiting(item)}
                              disabled={removingId === item.id}
                              title="Remove este vídeo do banco e dos agendamentos vinculados"
                            >
                              {removingId === item.id ? 'Excluindo...' : 'Excluir'}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="banco-block">
              <h2>Vídeos Postados</h2>
              {postedItems.length === 0 ? (
                <p className="empty-msg">Nenhum vídeo postado ainda.</p>
              ) : (
                <div className="banco-table-wrap">
                  <table className="banco-table">
                    <thead>
                      <tr>
                        <th>Brand</th>
                        <th>Tipo</th>
                        <th>Título</th>
                        <th>Score</th>
                        <th>Fonte</th>
                        <th>Postado em</th>
                      </tr>
                    </thead>
                    <tbody>
                      {postedItems.map((item) => (
                        <tr key={item.id}>
                          <td>{brandNameById[String(item.brand)] || `Brand #${item.brand}`}</td>
                          <td>{TYPE_LABEL[item.video_type] || item.video_type || '-'}</td>
                          <td className="banco-title">{item.title || '-'}</td>
                          <td>{item.virality_score ?? '-'}</td>
                          <td>{item.source_display_name || item.source_asset_id || '-'}</td>
                          <td>{formatDate(item.posted_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
