import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  cancelMultipleCreatorJob,
  createMultipleCreator,
  deleteMultipleCreatorJob,
  getBrandsAllPages,
  getFactories,
  getSourcesAllPages,
  listMultipleCreatorJobs,
  retryMultipleCreatorBrand,
} from '../api'
import PaginationControls from '../components/PaginationControls'
import './MultipleCreator.css'

const JOB_STATUS_LABEL = {
  PENDING_TRANSCRIPTION: 'Aguardando transcrição',
  TRANSCRIBING: 'Transcrevendo',
  READY: 'Pronto para fanout',
  RUNNING_BRANDS: 'Processando brands',
  DONE: 'Concluído',
  PARTIAL: 'Concluído com falhas',
  ERROR: 'Erro',
}
const BRAND_EXEC_STATUS_LABEL = {
  PENDING: 'Pendente',
  ANALYZING: 'Analisando',
  FINALIZING: 'Finalizando',
  DONE: 'Concluído',
  ERROR: 'Erro',
}
const TERMINAL_JOB_STATUS = new Set(['DONE', 'PARTIAL', 'ERROR'])
const RETRYABLE_BRAND_STATUS = new Set(['DONE', 'ERROR'])
const JOBS_PAGE_SIZE = 10

function formatDateTime(value) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '-'
  return d.toLocaleString('pt-BR')
}


const VERTICAL_MODES = [
  { value: 'zoom_crop', label: 'Zoom + crop' },
  { value: 'blur_background', label: 'Blur background' },
]

const THUMBNAIL_FONTS = [
  { value: 'impact', label: 'Impact' },
  { value: 'arial-black', label: 'Arial Black' },
]

export default function MultipleCreator() {
  const { brandId } = useBrand()

  const [brands, setBrands] = useState([])
  const [factories, setFactories] = useState([])
  const [sources, setSources] = useState([])

  const [file, setFile] = useState(null)
  const [sourceId, setSourceId] = useState('')
  const [youtubeUrl, setYoutubeUrl] = useState('')

  const [selectedBrandIds, setSelectedBrandIds] = useState(() => new Set())
  const [brandSearch, setBrandSearch] = useState('')

  const [name, setName] = useState('')
  const [assunto, setAssunto] = useState('')
  const [convidados, setConvidados] = useState('')
  const [promptVersion, setPromptVersion] = useState('viral')
  const [verticalMode, setVerticalMode] = useState('zoom_crop')
  const [shortsTarget, setShortsTarget] = useState(12)
  const [longsTarget, setLongsTarget] = useState(3)

  const [thumbnailFont, setThumbnailFont] = useState('impact')
  const [thumbnailBandColor, setThumbnailBandColor] = useState('#E12E20')
  const [thumbnailTextColor, setThumbnailTextColor] = useState('#0A0A0A')
  const [thumbnailStrokeColor, setThumbnailStrokeColor] = useState('#FFEBDC')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const [jobs, setJobs] = useState([])
  const [jobsPage, setJobsPage] = useState(1)
  const [jobsTotal, setJobsTotal] = useState(0)
  const [jobsLoading, setJobsLoading] = useState(false)
  const [retryingKey, setRetryingKey] = useState('')
  const [cancellingId, setCancellingId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const jobsReqIdRef = useRef(0)

  useEffect(() => {
    getBrandsAllPages().then(setBrands).catch(() => setBrands([]))
    getFactories().then(setFactories).catch(() => setFactories([]))
  }, [])

  useEffect(() => {
    if (!brandId) {
      setSources([])
      return
    }
    getSourcesAllPages(brandId).then(setSources).catch(() => setSources([]))
  }, [brandId])

  const loadJobs = useCallback(async () => {
    const reqId = ++jobsReqIdRef.current
    setJobsLoading(true)
    try {
      const paged = await listMultipleCreatorJobs({ page: jobsPage, pageSize: JOBS_PAGE_SIZE })
      if (reqId !== jobsReqIdRef.current) return
      setJobs(paged.items)
      setJobsTotal(paged.count)
    } catch (err) {
      if (reqId !== jobsReqIdRef.current) return
      setJobs([])
      setJobsTotal(0)
    } finally {
      if (reqId === jobsReqIdRef.current) setJobsLoading(false)
    }
  }, [jobsPage])

  useEffect(() => {
    loadJobs()
  }, [loadJobs])

  const hasNonTerminalJob = useMemo(
    () => jobs.some((j) => !TERMINAL_JOB_STATUS.has(j.status)),
    [jobs],
  )

  useEffect(() => {
    if (!hasNonTerminalJob) return undefined
    const id = setInterval(() => {
      loadJobs()
    }, 5000)
    return () => clearInterval(id)
  }, [hasNonTerminalJob, loadJobs])

  const brandNameById = useMemo(
    () => Object.fromEntries((brands || []).map((b) => [String(b.id), b.name])),
    [brands],
  )

  const factoryNameById = useMemo(
    () => Object.fromEntries((factories || []).map((f) => [String(f.id), f.name])),
    [factories],
  )

  const groupedBrands = useMemo(() => {
    const groups = new Map()
    const term = brandSearch.trim().toLowerCase()
    ;(brands || []).forEach((b) => {
      if (term && !String(b.name || '').toLowerCase().includes(term)) return
      const factoryKey = b.factory ? String(b.factory) : ''
      const factoryLabel = factoryKey
        ? factoryNameById[factoryKey] || `Factory #${factoryKey}`
        : 'Sem factory'
      if (!groups.has(factoryLabel)) groups.set(factoryLabel, [])
      groups.get(factoryLabel).push(b)
    })
    return Array.from(groups.entries())
      .map(([label, items]) => [label, items.sort((a, b) => a.name.localeCompare(b.name))])
      .sort(([a], [b]) => a.localeCompare(b))
  }, [brands, brandSearch, factoryNameById])

  function toggleBrand(id) {
    setSelectedBrandIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function selectAllVisible() {
    const visibleIds = groupedBrands.flatMap(([, items]) => items.map((b) => b.id))
    setSelectedBrandIds((prev) => {
      const next = new Set(prev)
      visibleIds.forEach((id) => next.add(id))
      return next
    })
  }

  function clearSelection() {
    setSelectedBrandIds(new Set())
  }

  function sourcesCount() {
    let n = 0
    if (file) n += 1
    if (sourceId) n += 1
    if (youtubeUrl.trim()) n += 1
    return n
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setInfo('')

    const n = sourcesCount()
    if (n === 0) {
      setError('Selecione um arquivo, uma fonte ou informe uma URL do YouTube.')
      return
    }
    if (n > 1) {
      setError('Escolha apenas uma origem: arquivo, fonte ou URL do YouTube.')
      return
    }
    if (selectedBrandIds.size === 0) {
      setError('Selecione ao menos uma brand.')
      return
    }

    setSubmitting(true)
    try {
      const job = await createMultipleCreator({
        file: file || undefined,
        sourceId: sourceId || undefined,
        youtubeUrl: youtubeUrl.trim() || undefined,
        brandIds: Array.from(selectedBrandIds),
        name: name.trim() || undefined,
        assunto: assunto.trim() || undefined,
        convidados: convidados.trim() || undefined,
        promptVersion,
        verticalMode,
        shortsTarget,
        longsTarget,
        thumbnailFont,
        thumbnailBandColor,
        thumbnailTextColor,
        thumbnailStrokeColor,
      })
      const n = (job?.brand_executions || []).length
      setInfo(
        `Job #${job.id} criado com ${n} execução(ões). ` +
        'Transcrição única em andamento; o fanout por brand começa logo após.',
      )
      if (jobsPage !== 1) {
        setJobsPage(1)
      } else {
        loadJobs()
      }
    } catch (err) {
      setError(err?.message || 'Erro ao enviar.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRetry(jobId, brandId) {
    const key = `${jobId}:${brandId}`
    setError('')
    setRetryingKey(key)
    try {
      const updated = await retryMultipleCreatorBrand(jobId, brandId)
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)))
    } catch (err) {
      setError(err?.message || 'Erro ao re-rodar a brand.')
    } finally {
      setRetryingKey('')
    }
  }

  async function handleCancel(jobId) {
    setError('')
    setCancellingId(jobId)
    try {
      const updated = await cancelMultipleCreatorJob(jobId)
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)))
    } catch (err) {
      setError(err?.message || 'Erro ao cancelar o job.')
    } finally {
      setCancellingId(null)
    }
  }

  async function handleDelete(jobId) {
    if (!window.confirm('Excluir este job permanentemente?')) return
    setError('')
    setDeletingId(jobId)
    try {
      await deleteMultipleCreatorJob(jobId)
      setJobs((prev) => prev.filter((j) => j.id !== jobId))
      setJobsTotal((prev) => Math.max(0, prev - 1))
    } catch (err) {
      setError(err?.message || 'Erro ao excluir o job.')
    } finally {
      setDeletingId(null)
    }
  }

  const selectedCount = selectedBrandIds.size

  return (
    <div className="multiple-creator">
      <h1>Multiple-Creator</h1>
      <p className="page-desc">
        Envie um único vídeo e gere cortes em paralelo para várias brands. A transcrição roda uma única vez;
        cada brand recebe uma análise individual no LLM para evitar títulos e hooks repetidos entre canais.
      </p>

      {error && <div className="form-error">{error}</div>}
      {info && <div className="form-info">{info}</div>}

      <form onSubmit={handleSubmit} className="mc-form">
        <fieldset className="mc-fieldset">
          <legend>Origem do vídeo</legend>
          <div className="mc-row">
            <label>
              Arquivo
              <input
                type="file"
                accept="video/*"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </label>
            <label>
              Fonte existente (da brand do contexto)
              <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">—</option>
                {sources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title || s.original_filename || `Source #${s.id}`}
                  </option>
                ))}
              </select>
            </label>
            <label>
              URL do YouTube
              <input
                type="url"
                placeholder="https://youtube.com/..."
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
              />
            </label>
          </div>
          <p className="mc-hint">Escolha exatamente uma das três opções acima.</p>
        </fieldset>

        <fieldset className="mc-fieldset">
          <legend>
            Brands selecionadas{' '}
            <span className="mc-chip">{selectedCount}</span>
          </legend>
          <div className="mc-row mc-brands-toolbar">
            <input
              type="search"
              placeholder="Buscar brand..."
              value={brandSearch}
              onChange={(e) => setBrandSearch(e.target.value)}
              className="mc-search"
            />
            <button type="button" className="btn-action" onClick={selectAllVisible}>
              Selecionar visíveis
            </button>
            <button type="button" className="btn-action btn-cancel" onClick={clearSelection}>
              Limpar
            </button>
          </div>
          <div className="mc-brands-grid">
            {groupedBrands.length === 0 ? (
              <p className="empty-msg">Nenhuma brand encontrada.</p>
            ) : (
              groupedBrands.map(([factoryLabel, items]) => (
                <div key={factoryLabel} className="mc-brands-group">
                  <h4>{factoryLabel}</h4>
                  <ul>
                    {items.map((b) => (
                      <li key={b.id}>
                        <label className="mc-brand-check">
                          <input
                            type="checkbox"
                            checked={selectedBrandIds.has(b.id)}
                            onChange={() => toggleBrand(b.id)}
                          />
                          <span>{b.name}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </div>
        </fieldset>

        <fieldset className="mc-fieldset">
          <legend>Metadados compartilhados</legend>
          <div className="mc-row">
            <label>
              Nome do job
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              Assunto
              <input value={assunto} onChange={(e) => setAssunto(e.target.value)} />
            </label>
            <label>
              Convidados
              <input value={convidados} onChange={(e) => setConvidados(e.target.value)} />
            </label>
          </div>
          <div className="mc-row">
            <label>
              Prompt
              <select value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)}>
                <option value="educational">Educacional (PT, 2–3 min)</option>
                <option value="viral">Viral (PT, 30–60 seg)</option>
                <option value="viral_long">Viral longo (PT, 90–160 seg)</option>
                <option value="educational_en">Educacional (EN, 2–3 min)</option>
                <option value="viral_en">Viral (EN, 30–60 seg)</option>
                <option value="viral_long_en">Viral longo (EN, 90–160 seg)</option>
                <option value="viral_translate">Viral Translate (EN→PT)</option>
              </select>
            </label>
            <label>
              Vertical mode
              <select value={verticalMode} onChange={(e) => setVerticalMode(e.target.value)}>
                {VERTICAL_MODES.map((v) => (
                  <option key={v.value} value={v.value}>{v.label}</option>
                ))}
              </select>
            </label>
            <label>
              Shorts target
              <input
                type="number"
                min={0}
                value={shortsTarget}
                onChange={(e) => setShortsTarget(Number(e.target.value) || 0)}
              />
            </label>
            <label>
              Longs target
              <input
                type="number"
                min={0}
                value={longsTarget}
                onChange={(e) => setLongsTarget(Number(e.target.value) || 0)}
              />
            </label>
          </div>
        </fieldset>

        <fieldset className="mc-fieldset">
          <legend>Thumbnail</legend>
          <div className="mc-row">
            <label>
              Fonte
              <select value={thumbnailFont} onChange={(e) => setThumbnailFont(e.target.value)}>
                {THUMBNAIL_FONTS.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </label>
            <label>
              Cor da banda
              <input type="color" value={thumbnailBandColor} onChange={(e) => setThumbnailBandColor(e.target.value)} />
            </label>
            <label>
              Cor do texto
              <input type="color" value={thumbnailTextColor} onChange={(e) => setThumbnailTextColor(e.target.value)} />
            </label>
            <label>
              Cor do contorno
              <input type="color" value={thumbnailStrokeColor} onChange={(e) => setThumbnailStrokeColor(e.target.value)} />
            </label>
          </div>
        </fieldset>

        <div className="mc-actions">
          <button type="submit" className="btn-action btn-primary" disabled={submitting}>
            {submitting ? 'Enviando...' : `Gerar para ${selectedCount || 0} brand(s)`}
          </button>
        </div>
      </form>

      <section className="mc-jobs">
        <header className="mc-jobs-header">
          <h2>Jobs recentes</h2>
          <button
            type="button"
            className="btn-action"
            onClick={() => loadJobs()}
            disabled={jobsLoading}
          >
            {jobsLoading ? 'Atualizando...' : 'Atualizar'}
          </button>
        </header>

        {jobs.length === 0 && !jobsLoading ? (
          <p className="empty-msg">Nenhum job ainda. Submeta o formulário acima para começar.</p>
        ) : (
          <div className="mc-jobs-list">
            {jobs.map((job) => (
              <article key={job.id} className={`mc-job mc-job-${job.status}`}>
                <div className="mc-job-head">
                  <div>
                    <strong>#{job.id}</strong> {job.name || '(sem nome)'}
                    <span className="mc-job-meta"> · {job.source_kind}</span>
                  </div>
                  <div className="mc-job-head-right">
                    <div className="mc-job-status">
                      {JOB_STATUS_LABEL[job.status] || job.status}
                      {job.progress != null && job.progress < 100 && job.progress > 0 && (
                        <span className="mc-job-progress"> ({job.progress}%)</span>
                      )}
                    </div>
                    <div className="mc-job-actions">
                      {!TERMINAL_JOB_STATUS.has(job.status) && (
                        <button
                          type="button"
                          className="btn-action btn-cancel"
                          onClick={() => handleCancel(job.id)}
                          disabled={cancellingId === job.id}
                          title="Cancelar este job"
                        >
                          {cancellingId === job.id ? 'Cancelando...' : 'Cancelar'}
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-action btn-danger"
                        onClick={() => handleDelete(job.id)}
                        disabled={deletingId === job.id}
                        title="Excluir este job permanentemente"
                      >
                        {deletingId === job.id ? 'Excluindo...' : 'Excluir'}
                      </button>
                    </div>
                  </div>
                </div>
                {job.progress_message && (
                  <p className="mc-job-msg">{job.progress_message}</p>
                )}
                {job.error && <p className="form-error mc-job-msg">{job.error}</p>}
                <table className="mc-exec-table">
                  <thead>
                    <tr>
                      <th>Brand</th>
                      <th>Status</th>
                      <th>Erro</th>
                      <th>Iniciado</th>
                      <th>Finalizado</th>
                      <th>Ações</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(job.brand_executions || []).map((ex) => {
                      const brandName = brandNameById[String(ex.brand)] || `Brand #${ex.brand}`
                      const canRetry = RETRYABLE_BRAND_STATUS.has(ex.status)
                      const retryKey = `${job.id}:${ex.brand}`
                      return (
                        <tr key={ex.id}>
                          <td>{brandName}</td>
                          <td>{BRAND_EXEC_STATUS_LABEL[ex.status] || ex.status}</td>
                          <td className="mc-error-cell" title={ex.error || ''}>
                            {ex.error || '-'}
                          </td>
                          <td>{formatDateTime(ex.started_at)}</td>
                          <td>{formatDateTime(ex.finished_at)}</td>
                          <td>
                            <button
                              type="button"
                              className="btn-action"
                              onClick={() => handleRetry(job.id, ex.brand)}
                              disabled={!canRetry || retryingKey === retryKey}
                              title={
                                canRetry
                                  ? 'Re-roda esta brand reaproveitando a transcrição'
                                  : 'Indisponível enquanto a execução está em andamento'
                              }
                            >
                              {retryingKey === retryKey ? 'Re-rodando...' : 'Retry'}
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </article>
            ))}
          </div>
        )}

        {jobsTotal > JOBS_PAGE_SIZE && (
          <PaginationControls
            page={jobsPage}
            pageSize={JOBS_PAGE_SIZE}
            totalCount={jobsTotal}
            onPageChange={setJobsPage}
            disabled={jobsLoading}
          />
        )}
      </section>
    </div>
  )
}
