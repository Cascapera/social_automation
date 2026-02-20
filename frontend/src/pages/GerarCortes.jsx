import { useState, useEffect } from 'react'
import { useBrand } from '../context/BrandContext'
import { getSources, getCuts, uploadSourceWithProgress, extractCuts, deleteCut, uploadCut } from '../api'
import './GerarCortes.css'

function secondsToTC(sec) {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function GerarCortes() {
  const { brandId } = useBrand()
  const [sources, setSources] = useState([])
  const [cuts, setCuts] = useState([])
  const [title, setTitle] = useState('')
  const [file, setFile] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [phase, setPhase] = useState(null)
  const [error, setError] = useState('')
  const [cutsForm, setCutsForm] = useState([{ name: '', start_tc: '', end_tc: '', format: 'vertical' }])
  const [previewCut, setPreviewCut] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [uploadCutFile, setUploadCutFile] = useState(null)
  const [uploadCutName, setUploadCutName] = useState('')
  const [uploadCutFormat, setUploadCutFormat] = useState('vertical')
  const [uploadingCut, setUploadingCut] = useState(false)

  useEffect(() => {
    if (brandId) {
      getSources(brandId).then(setSources).catch(() => setSources([]))
    } else {
      setSources([])
    }
  }, [brandId])

  useEffect(() => {
    if (brandId) {
      getCuts(null, brandId).then(setCuts).catch(() => setCuts([]))
    } else {
      setCuts([])
    }
  }, [brandId])

  function handleFileSelect(selectedFile) {
    setFile(selectedFile)
    setError('')
    if (!selectedFile) {
      setCutsForm([{ name: '', start_tc: '', end_tc: '', format: 'vertical' }])
      return
    }
    const video = document.createElement('video')
    video.preload = 'metadata'
    video.onloadedmetadata = () => {
      const duration = video.duration
      setCutsForm([{ name: '', start_tc: '00:00:00', end_tc: secondsToTC(duration), format: 'vertical' }])
      URL.revokeObjectURL(video.src)
    }
    video.onerror = () => {
      setCutsForm([{ name: '', start_tc: '00:00:00', end_tc: '', format: 'vertical' }])
    }
    video.src = URL.createObjectURL(selectedFile)
  }

  function addCut() {
    setCutsForm([...cutsForm, { name: '', start_tc: '', end_tc: '', format: 'vertical' }])
  }

  function removeCut(i) {
    setCutsForm(cutsForm.filter((_, idx) => idx !== i))
  }

  function updateCut(i, field, value) {
    const next = [...cutsForm]
    next[i] = { ...next[i], [field]: value }
    setCutsForm(next)
  }

  async function handleImportAndExtract(e) {
    e.preventDefault()
    if (!brandId) {
      setError('Selecione uma marca no menu à esquerda')
      return
    }
    if (!title || !file) {
      setError('Preencha título e selecione o vídeo')
      return
    }
    const valid = cutsForm.filter((c) => c.start_tc && c.end_tc)
    if (valid.length === 0) {
      setError('Adicione pelo menos um corte com início e fim')
      return
    }
    setError('')
    setPhase('upload')
    setUploadProgress(0)
    try {
      const s = await uploadSourceWithProgress(brandId, title, file, setUploadProgress)
      setPhase('extract')
      const created = await extractCuts(
        s.id,
        valid.map((c) => ({
          name: c.name || '',
          start_tc: c.start_tc,
          end_tc: c.end_tc,
          format: c.format || 'vertical',
        }))
      )
      setCuts((prev) => [...created, ...prev])
      setSources((prev) => prev.filter((src) => src.id !== s.id))
      setFile(null)
      setTitle('')
      setCutsForm([{ name: '', start_tc: '', end_tc: '', format: 'vertical' }])
    } catch (e) {
      setError(e.message)
    } finally {
      setPhase(null)
      setUploadProgress(0)
    }
  }

  const formatLabel = (f) => (f === 'vertical' ? 'Vertical (9:16)' : 'Horizontal (16:9)')

  function formatDuration(sec) {
    if (sec == null || sec === undefined) return '-'
    const m = Math.floor(sec / 60)
    const s = Math.floor(sec % 60)
    return m > 0 ? `${m}min ${s}s` : `${s}s`
  }

  async function handleUploadCut(e) {
    e.preventDefault()
    if (!brandId) {
      setError('Selecione uma marca no menu à esquerda')
      return
    }
    if (!uploadCutFile) {
      setError('Selecione um arquivo de vídeo')
      return
    }
    setError('')
    setUploadingCut(true)
    try {
      const c = await uploadCut(uploadCutFile, uploadCutName, uploadCutFormat, brandId)
      setCuts((prev) => [c, ...prev])
      setUploadCutFile(null)
      setUploadCutName('')
      setUploadCutFormat('vertical')
    } catch (e) {
      setError(e.message)
    } finally {
      setUploadingCut(false)
    }
  }

  async function handleDeleteCut(id) {
    if (!confirm('Deletar este corte? O arquivo será removido permanentemente.')) return
    setDeleting(id)
    try {
      await deleteCut(id)
      setCuts((prev) => prev.filter((c) => c.id !== id))
    } catch (e) {
      setError(e.message)
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="gerar-cortes">
      <h1>Gerar cortes</h1>
      <p className="page-desc">
        Envie um corte pronto ou gere cortes a partir de um vídeo. O vídeo original será deletado após a extração.
      </p>

      {error && <div className="form-error">{error}</div>}

      <section className="section">
        <h2>1. Upload de corte pronto</h2>
        <p className="section-hint">Já tem o corte editado? Envie o arquivo. O formato (vertical/horizontal) será detectado automaticamente.</p>
        <form onSubmit={handleUploadCut} className="form-inline">
          <input
            type="text"
            placeholder="Nome (opcional)"
            value={uploadCutName}
            onChange={(e) => setUploadCutName(e.target.value)}
          />
          <select value={uploadCutFormat} onChange={(e) => setUploadCutFormat(e.target.value)}>
            <option value="vertical">Vertical</option>
            <option value="horizontal">Horizontal</option>
          </select>
          <input
            type="file"
            accept="video/*"
            onChange={(e) => setUploadCutFile(e.target.files?.[0] || null)}
            required
          />
          <button type="submit" disabled={uploadingCut}>
            {uploadingCut ? 'Analisando...' : 'Enviar corte'}
          </button>
        </form>
      </section>

      <section className="section">
        <h2>Ou: Gerar cortes a partir de um vídeo</h2>
        <p className="section-hint">Selecione o vídeo, defina os cortes (nome e timestamps) e clique em Importar e extrair. O envio e a extração ocorrem em sequência.</p>
        {!brandId && (
          <p className="form-hint">Selecione uma marca no menu à esquerda para continuar.</p>
        )}
        <form onSubmit={handleImportAndExtract} className="cuts-form">
          <div className="form-inline form-source">
            <input
              type="text"
              placeholder="Título"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
            <input
              type="file"
              accept="video/*"
              onChange={(e) => handleFileSelect(e.target.files?.[0] || null)}
              required
            />
          </div>
          <div className="cuts-definition">
            <h3>Definir cortes (formato HH:MM:SS)</h3>
            {cutsForm.map((c, i) => (
              <div key={i} className="cut-row">
                <input
                  placeholder="Nome"
                  value={c.name}
                  onChange={(e) => updateCut(i, 'name', e.target.value)}
                />
                <input
                  placeholder="Início"
                  value={c.start_tc}
                  onChange={(e) => updateCut(i, 'start_tc', e.target.value)}
                />
                <input
                  placeholder="Fim"
                  value={c.end_tc}
                  onChange={(e) => updateCut(i, 'end_tc', e.target.value)}
                />
                <select value={c.format} onChange={(e) => updateCut(i, 'format', e.target.value)}>
                  <option value="vertical">Vertical</option>
                  <option value="horizontal">Horizontal</option>
                </select>
                <button type="button" onClick={() => removeCut(i)} className="btn-remove">✕</button>
              </div>
            ))}
            <button type="button" onClick={addCut} className="btn-add">+ Adicionar corte</button>
          </div>
          {(phase === 'upload' || phase === 'extract') && (
            <div className="progress-container">
              <div className="progress-bar">
                <div
                  className="progress-fill"
                  style={{ width: phase === 'upload' ? `${uploadProgress}%` : '100%' }}
                />
              </div>
              <span className="progress-label">
                {phase === 'upload' ? `Enviando vídeo... ${uploadProgress}%` : 'Extraindo cortes...'}
              </span>
            </div>
          )}
          <button type="submit" disabled={!!phase}>
            {phase ? 'Processando...' : 'Importar e extrair'}
          </button>
        </form>
      </section>

      <section className="section">
        <h2>Cortes salvos</h2>
        {cuts.length === 0 ? (
          <p className="empty-msg">Nenhum corte ainda.</p>
        ) : (
          <div className="cuts-table">
            <div className="cuts-header">
              <span>ID</span>
              <span>Nome</span>
              <span>Tempo</span>
              <span>Duração</span>
              <span>Formato</span>
              <span></span>
            </div>
            {cuts.map((c) => (
              <div key={c.id} className="cuts-row">
                <span>{c.id}</span>
                <span>{c.name || '-'}</span>
                <span>{c.start_tc} → {c.end_tc}</span>
                <span>{formatDuration(c.duration)}</span>
                <span>{formatLabel(c.format)}</span>
                <span className="cuts-actions">
                  {c.file_url && (
                    <button
                      type="button"
                      className="btn-preview"
                      onClick={() => setPreviewCut(c)}
                      title="Visualizar"
                    >
                      ▶
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-delete-cut"
                    onClick={() => handleDeleteCut(c.id)}
                    disabled={deleting === c.id}
                    title="Deletar"
                  >
                    {deleting === c.id ? '...' : '🗑'}
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {previewCut && (
        <div className="preview-modal" onClick={() => setPreviewCut(null)}>
          <div className="preview-modal-content" onClick={(e) => e.stopPropagation()}>
            <button type="button" className="preview-close" onClick={() => setPreviewCut(null)}>✕</button>
            <h3>{previewCut.name || `Corte #${previewCut.id}`}</h3>
            <video
              src={previewCut.file_url}
              controls
              autoPlay
              playsInline
              onError={() => setError('Não foi possível carregar o vídeo')}
            />
          </div>
        </div>
      )}
    </div>
  )
}
