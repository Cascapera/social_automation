import { useEffect, useMemo, useState } from 'react'
import { useBrand } from '../context/BrandContext'
import { getVideoInventory, removeAwaitingInventoryItem, retryAwaitingInventoryItem, downloadInventoryMedia, markInventoryPosted } from '../api'
import PaginationControls, { DEFAULT_PAGE_SIZE } from '../components/PaginationControls'
import './BancoVideos.css'

const STATUS_LABEL = {
  AVAILABLE: 'Disponível',
  SCHEDULED: 'Postando',
  POSTING: 'Postando',
  POSTED: 'Postado',
  FAILED: 'Erro',
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

/** Formata data curta dd/MM/yyyy HH:mm para exibição de agendamento. */
function formatScheduledShort(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const day = String(d.getDate()).padStart(2, '0')
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const year = d.getFullYear()
  const h = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${day}/${month}/${year} ${h}:${min}`
}

function statusDisplay(item) {
  const base = STATUS_LABEL[item.status] || item.status || '-'
  const msg = item.status_message
  if (item.status === 'FAILED') {
    return base
  }
  if (item.status === 'SCHEDULED' || item.status === 'POSTING') {
    return msg ? `${base} (${msg})` : base
  }
  return base
}

/** Formata data para input datetime-local (YYYY-MM-DDTHH:mm) em horário local. */
function toDatetimeLocal(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day}T${h}:${min}`
}

function slugifyDownloadName(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export default function BancoVideos() {
  const { viewMode, factoryId, brandId, brands } = useBrand()
  const [items, setItems] = useState([])
  const [videoType, setVideoType] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [removingId, setRemovingId] = useState(null)
  const [retryingId, setRetryingId] = useState(null)
  const [downloadingId, setDownloadingId] = useState(null)
  const [markingPostedId, setMarkingPostedId] = useState(null)
  const [retryModalItem, setRetryModalItem] = useState(null)
  const [retryScheduledAt, setRetryScheduledAt] = useState('')
  const [markPostedModalItem, setMarkPostedModalItem] = useState(null)
  const [markPostedAt, setMarkPostedAt] = useState('')
  const [inventoryPage, setInventoryPage] = useState(1)
  const [inventoryTotal, setInventoryTotal] = useState(0)

  useEffect(() => {
    setInventoryPage(1)
  }, [viewMode, factoryId, brandId, videoType])

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
        const paged = await getVideoInventory({
          factoryId: effectiveFactoryId,
          brandId: effectiveBrandId,
          videoType: videoType || null,
          page: inventoryPage,
          pageSize: DEFAULT_PAGE_SIZE,
        })
        if (!cancelled) {
          setItems(paged.items)
          setInventoryTotal(paged.count)
        }
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
  }, [viewMode, factoryId, brandId, videoType, inventoryPage])

  const brandNameById = useMemo(
    () => Object.fromEntries((brands || []).map((b) => [String(b.id), b.name])),
    [brands],
  )

  const summary = useMemo(() => {
    const acc = { total: inventoryTotal, AVAILABLE: 0, SCHEDULED: 0, POSTING: 0, POSTED: 0, FAILED: 0 }
    items.forEach((item) => {
      if (acc[item.status] != null) acc[item.status] += 1
    })
    return acc
  }, [items, inventoryTotal])

  const awaitingItems = useMemo(
    () => items.filter((item) => item.status !== 'POSTED'),
    [items],
  )
  const postedItems = useMemo(
    () => items.filter((item) => item.status === 'POSTED'),
    [items],
  )

  const needsContext =
    (viewMode === 'factory' && !factoryId) || (viewMode === 'brand' && !brandId)

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

  function openRetryModal(item) {
    if (!item?.id) return
    const initial = item.scheduled_for ? toDatetimeLocal(item.scheduled_for) : toDatetimeLocal(new Date())
    setRetryModalItem(item)
    setRetryScheduledAt(initial || toDatetimeLocal(new Date()))
    setError('')
  }

  function openMarkPostedModal(item) {
    if (!item?.id) return
    const initial = toDatetimeLocal(new Date())
    setMarkPostedModalItem(item)
    setMarkPostedAt(initial || '')
    setError('')
  }

  function closeMarkPostedModal() {
    if (markingPostedId != null) return
    setMarkPostedModalItem(null)
    setMarkPostedAt('')
  }

  async function handleDownloadMedia(item) {
    if (!item?.id) return
    setError('')
    setDownloadingId(item.id)
    try {
      const brandSlug = slugifyDownloadName(brandNameById[String(item.brand)] || `brand-${item.brand}`)
      await downloadInventoryMedia(item.id, `${brandSlug || 'brand'}-${item.id}`)
    } catch (e) {
      setError(e.message || 'Erro ao baixar mídias.')
    } finally {
      setDownloadingId(null)
    }
  }

  async function handleMarkPosted(item, postedAtValue) {
    if (!item?.id) return
    if (!postedAtValue) {
      setError('Informe a data e hora da postagem.')
      return
    }
    const postedAtDate = new Date(postedAtValue)
    if (Number.isNaN(postedAtDate.getTime())) {
      setError('Informe uma data e hora válidas para a postagem.')
      return
    }
    setError('')
    setMarkingPostedId(item.id)
    try {
      const payload = { posted_at: postedAtDate.toISOString() }
      const result = await markInventoryPosted(item.id, payload)
      setItems((prev) =>
        prev.map((x) =>
          x.id === item.id
            ? {
                ...x,
                status: 'POSTED',
                posted_at: result?.posted_at || payload.posted_at,
                last_error: '',
              }
            : x,
        ),
      )
      setMarkPostedModalItem(null)
      setMarkPostedAt('')
    } catch (e) {
      setError(e.message || 'Erro ao marcar como postado.')
    } finally {
      setMarkingPostedId(null)
    }
  }

  async function handleRetryPosting(item, scheduledAtValue) {
    if (!item?.id) return
    setError('')
    setRetryingId(item.id)
    if (retryModalItem?.id === item.id) setRetryModalItem(null)
    try {
      const payload = scheduledAtValue
        ? { scheduled_at: new Date(scheduledAtValue).toISOString() }
        : {}
      const result = await retryAwaitingInventoryItem(item.id, payload)
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
          {viewMode === 'factory'
            ? 'Selecione uma factory no menu lateral para visualizar o inventário.'
            : 'Selecione uma marca no menu lateral para visualizar o inventário.'}
        </div>
      )}

      {error && <div className="form-error">{error}</div>}

      {retryModalItem && (
        <div className="banco-modal-overlay" onClick={() => setRetryModalItem(null)} role="presentation">
          <div className="banco-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={retryModalItem?.status === 'AVAILABLE' ? 'Agendar vídeo' : 'Reagendar e tentar novamente'}>
            <h3>{retryModalItem?.status === 'AVAILABLE' ? 'Agendar vídeo' : 'Tentar novamente'}</h3>
            <p className="banco-modal-desc">
              {retryModalItem?.status === 'AVAILABLE'
                ? 'Escolha a data e horário para postar este vídeo. O sistema enviará e agendará no YouTube.'
                : 'Escolha o horário para a nova tentativa. O horário atual está preenchido; você pode alterá-lo.'}
            </p>
            <label className="banco-modal-label">
              Horário para tentativa
              <input
                type="datetime-local"
                value={retryScheduledAt}
                onChange={(e) => setRetryScheduledAt(e.target.value)}
                min={toDatetimeLocal(new Date())}
                className="banco-modal-input"
              />
            </label>
            <div className="banco-modal-actions">
              <button
                type="button"
                className="btn-action"
                disabled={retryingId === retryModalItem.id}
                onClick={() => handleRetryPosting(retryModalItem, retryScheduledAt || undefined)}
              >
                {retryingId === retryModalItem.id ? 'Tentando...' : 'Tentar novamente'}
              </button>
              <button
                type="button"
                className="btn-action btn-cancel"
                onClick={() => setRetryModalItem(null)}
                disabled={retryingId === retryModalItem.id}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      {markPostedModalItem && (
        <div className="banco-modal-overlay" onClick={closeMarkPostedModal} role="presentation">
          <div className="banco-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Marcar vídeo como postado">
            <h3>Marcar como postado</h3>
            <p className="banco-modal-desc">
              Informe a data e hora da postagem manual. Por padrão, o campo vem preenchido com o momento em que você clicou em "Postado".
            </p>
            <label className="banco-modal-label">
              Data e hora da postagem
              <input
                type="datetime-local"
                value={markPostedAt}
                onChange={(e) => setMarkPostedAt(e.target.value)}
                className="banco-modal-input"
              />
            </label>
            <div className="banco-modal-actions">
              <button
                type="button"
                className="btn-action btn-posted"
                disabled={markingPostedId === markPostedModalItem.id}
                onClick={() => handleMarkPosted(markPostedModalItem, markPostedAt)}
              >
                {markingPostedId === markPostedModalItem.id ? 'Marcando...' : 'Confirmar postagem'}
              </button>
              <button
                type="button"
                className="btn-action btn-cancel"
                onClick={closeMarkPostedModal}
                disabled={markingPostedId === markPostedModalItem.id}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      <section className="section banco-filters">
        <div className="banco-summary">
          <span>Total (registros): {summary.total}</span>
          <span>Aguardando (página): {awaitingItems.length}</span>
          <span>Postados (página): {postedItems.length}</span>
          <span>Disponíveis (página): {summary.AVAILABLE}</span>
          <span>Postando (página): {(summary.SCHEDULED || 0) + (summary.POSTING || 0)}</span>
          <span>Erros (página): {summary.FAILED}</span>
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
                        <th>Nº</th>
                        {viewMode === 'factory' && <th>Brand</th>}
                        <th>Tipo</th>
                        <th className="banco-titulo">Título</th>
                        <th>Score</th>
                        <th className="banco-fonte">Nome da fonte</th>
                        <th>Status</th>
                        <th>Erro</th>
                        <th>Ações</th>
                      </tr>
                    </thead>
                    <tbody>
                      {awaitingItems.map((item) => (
                        <tr key={item.id}>
                          <td title={item.scheduled_post_id ? `Vídeo #${item.id} • Post #${item.scheduled_post_id}` : `Vídeo #${item.id}`}>
                            {item.scheduled_post_id ? `Post #${item.scheduled_post_id}` : `Vídeo #${item.id}`}
                          </td>
                          {viewMode === 'factory' && (
                            <td>{brandNameById[String(item.brand)] || `Brand #${item.brand}`}</td>
                          )}
                          <td>{TYPE_LABEL[item.video_type] || item.video_type || '-'}</td>
                          <td className="banco-titulo" title={item.title || '-'}>{item.title || '-'}</td>
                          <td>{item.virality_score ?? '-'}</td>
                          <td className="banco-fonte" title={item.source_display_name || item.source_asset_id || '-'}>
                            {item.source_display_name || item.source_asset_id
                              ? `(${item.source_display_name || item.source_asset_id})`
                              : '-'}
                          </td>
                          <td>{statusDisplay(item)}</td>
                          <td className="banco-error">{item.last_error || '-'}</td>
                          <td>
                            <button
                              type="button"
                              className="btn-action"
                              onClick={() => openRetryModal(item)}
                              disabled={retryingId === item.id || item.status === 'POSTING'}
                              title={item.status === 'AVAILABLE' ? 'Agendar data e horário para postar este vídeo' : 'Tentar novamente e opcionalmente reagendar o horário'}
                              style={{ marginRight: 8 }}
                            >
                              {retryingId === item.id
                                ? (item.status === 'AVAILABLE' ? 'Agendando...' : 'Tentando...')
                                : item.status === 'AVAILABLE'
                                  ? 'Agendar'
                                  : 'Tentar novamente'}
                            </button>
                            <button
                              type="button"
                              className="btn-action btn-download"
                              onClick={() => handleDownloadMedia(item)}
                              disabled={downloadingId === item.id}
                              title="Baixar vídeo e thumbnail em um ZIP para postagem manual"
                              style={{ marginRight: 8 }}
                            >
                              {downloadingId === item.id ? 'Baixando...' : 'Download'}
                            </button>
                            <button
                              type="button"
                              className="btn-action btn-posted"
                              onClick={() => openMarkPostedModal(item)}
                              disabled={markingPostedId === item.id}
                              title="Informar data e hora e marcar como postado manualmente"
                              style={{ marginRight: 8 }}
                            >
                              {markingPostedId === item.id ? 'Marcando...' : 'Postado'}
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
                        {viewMode === 'factory' && <th>Brand</th>}
                        <th>Tipo</th>
                        <th className="banco-titulo">Título</th>
                        <th>Score</th>
                        <th className="banco-fonte">Nome da fonte</th>
                        <th>Postado em</th>
                      </tr>
                    </thead>
                    <tbody>
                      {postedItems.map((item) => (
                        <tr key={item.id}>
                          {viewMode === 'factory' && (
                            <td>{brandNameById[String(item.brand)] || `Brand #${item.brand}`}</td>
                          )}
                          <td>{TYPE_LABEL[item.video_type] || item.video_type || '-'}</td>
                          <td className="banco-titulo" title={item.title || '-'}>{item.title || '-'}</td>
                          <td>{item.virality_score ?? '-'}</td>
                          <td className="banco-fonte" title={item.source_display_name || item.source_asset_id || '-'}>
                            {item.source_display_name || item.source_asset_id
                              ? `(${item.source_display_name || item.source_asset_id})`
                              : '-'}
                          </td>
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
        {!loading && items.length > 0 && (
          <PaginationControls
            page={inventoryPage}
            totalCount={inventoryTotal}
            onPageChange={setInventoryPage}
            disabled={loading}
          />
        )}
      </section>
    </div>
  )
}
