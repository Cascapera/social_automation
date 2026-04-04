import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useBrand } from '../context/BrandContext'
import {
  getBrandSocialAccounts,
  getYoutubeConnectUrlForCredential,
  getBrandYoutubeCredentials,
  getYoutubePendingChannels,
  youtubeSelectChannel,
  deleteSocialAccount,
} from '../api'
import './Contas.css'

export default function Contas() {
  const { brandId } = useBrand()
  const [searchParams] = useSearchParams()
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadingCredentialId, setLoadingCredentialId] = useState('')
  const [error, setError] = useState('')
  const [chooseChannel, setChooseChannel] = useState(null)
  const [pendingKey, setPendingKey] = useState('')
  const [pendingChannels, setPendingChannels] = useState([])
  const [youtubeCredentials, setYoutubeCredentials] = useState([])

  const urlError = searchParams.get('error')
  const urlDetail = searchParams.get('detail')
  const youtubeConnected = searchParams.get('youtube_connected')
  const youtubeChooseChannel = searchParams.get('youtube_choose_channel')

  function loadAccounts() {
    if (!brandId) {
      setAccounts([])
      return Promise.resolve([])
    }
    return getBrandSocialAccounts(brandId)
      .then((items) => {
        setAccounts(items || [])
        return items || []
      })
      .catch(() => {
        setAccounts([])
        return []
      })
  }

  function loadYoutubeCredentials() {
    if (!brandId) {
      setYoutubeCredentials([])
      return Promise.resolve([])
    }
    return getBrandYoutubeCredentials(brandId)
      .then((items) => {
        setYoutubeCredentials(items || [])
        return items || []
      })
      .catch(() => {
        setYoutubeCredentials([])
        return []
      })
  }

  useEffect(() => {
    if (brandId) {
      loadAccounts()
      loadYoutubeCredentials()
    } else {
      setAccounts([])
      setYoutubeCredentials([])
    }
  }, [brandId])

  useEffect(() => {
    if (urlError) {
      const msg = urlDetail ? `${urlError}: ${urlDetail}` : urlError
      setError(msg)
    }
    if (youtubeConnected) {
      setError('')
      if (brandId) {
        loadAccounts()
        loadYoutubeCredentials()
      }
    }
    if (youtubeChooseChannel) {
      setPendingKey(searchParams.get('key') || '')
      setChooseChannel(true)
    }
  }, [urlError, urlDetail, youtubeConnected, youtubeChooseChannel, brandId])

  useEffect(() => {
    if (chooseChannel && pendingKey) {
      getYoutubePendingChannels(pendingKey)
        .then((d) => setPendingChannels(d.channels || []))
        .catch(() => setPendingChannels([]))
    } else {
      setPendingChannels([])
    }
  }, [chooseChannel, pendingKey])

  async function handleConnectYouTube(youtubeCredentialId = null) {
    if (!brandId) {
      setError('Selecione uma marca')
      return
    }
    setError('')
    setLoading(true)
    setLoadingCredentialId(youtubeCredentialId ? String(youtubeCredentialId) : '')
    try {
      const url = await getYoutubeConnectUrlForCredential(
        brandId,
        youtubeCredentialId || null,
      )
      window.location.href = url
    } catch (e) {
      setError(e.message)
      setLoading(false)
      setLoadingCredentialId('')
    }
  }

  async function handleSelectChannel(channelId, channelTitle) {
    setLoading(true)
    setError('')
    try {
      await youtubeSelectChannel(channelId, channelTitle, pendingKey)
      setChooseChannel(false)
      setPendingKey('')
      if (brandId) {
        loadAccounts()
        loadYoutubeCredentials()
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleDisconnectByCredential(cred) {
    const target = accounts.find(
      (acc) =>
        (acc.platform === 'YT' || acc.platform === 'YTB') &&
        String(acc.channel_id || '') === String(cred?.channel_id || ''),
    )
    if (!target?.id) {
      setError('Não foi possível localizar a conta conectada desta API para desconectar.')
      return
    }
    if (!confirm('Desconectar esta conta?')) return
    try {
      await deleteSocialAccount(target.id)
      if (brandId) {
        loadAccounts()
        loadYoutubeCredentials()
      }
    } catch (e) {
      setError(e.message)
    }
  }

  if (!brandId) {
    return (
      <div className="contas">
        <h1>Contas conectadas</h1>
        <p className="form-hint">Selecione uma marca no menu à esquerda para gerenciar contas.</p>
      </div>
    )
  }

  return (
    <div className="contas">
      <h1>Contas conectadas</h1>
      <p className="page-desc">
        Conecte suas contas de redes sociais para publicar automaticamente.
      </p>

      {error && <div className="form-error">{error}</div>}

      <section className="section">
        <h2>YouTube</h2>
        <p className="section-desc">
          Conecte seu canal do YouTube para publicar vídeos longos e Shorts.
        </p>
        {youtubeCredentials.length > 0 ? (
          <div className="youtube-credentials-list">
            {youtubeCredentials.map((cred, index) => (
              <div key={cred.id} className="youtube-credential-card">
                <div className="youtube-credential-head">
                  <strong>{cred.label || `API ${index + 1}`}</strong>
                  <span className={`youtube-credential-badge ${cred.is_connected ? 'ok' : 'warn'}`}>
                    {cred.is_connected ? 'Conectada' : 'Sem OAuth'}
                  </span>
                </div>
                <div className="youtube-credential-meta">
                  <span>Ordem: {cred.order_index}</span>
                  <span>Canal: {cred.account_name || cred.channel_id || '-'}</span>
                </div>
                <div className="youtube-credential-actions">
                  <button
                    type="button"
                    className="btn-connect"
                    onClick={() => handleConnectYouTube(cred.id)}
                    disabled={loading}
                  >
                    {loading && loadingCredentialId === String(cred.id)
                      ? 'Conectando...'
                      : `Conectar ${cred.label || `API ${index + 1}`}`}
                  </button>
                  {cred.is_connected && (
                    <button
                      type="button"
                      className="btn-disconnect"
                      onClick={() => handleDisconnectByCredential(cred)}
                    >
                      Desconectar desta API
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="form-hint">
            Nenhuma API cadastrada em `Brands` &gt; `YouTube credentials`.
          </p>
        )}

        {youtubeCredentials.some((c) => (c.last_error || '').trim()) && (
          <div className="credential-errors-block">
            <h3>Log de erros (última postagem)</h3>
            <p className="section-desc">
              Erros ocorridos ao publicar. O sistema tentou as outras credenciais automaticamente.
            </p>
            {youtubeCredentials
              .filter((c) => (c.last_error || '').trim())
              .map((cred) => (
                <div key={cred.id} className="credential-error-card">
                  <div className="credential-error-head">
                    <strong>{cred.label || `API #${cred.order_index}`}</strong>
                    <span className="credential-error-badge">
                      {cred.account_name || cred.channel_id || 'Canal'}
                    </span>
                  </div>
                  <div className="credential-error-msg">{cred.last_error}</div>
                  {cred.needs_reconnection && (
                    <div className="credential-error-warn">
                      ⚠️ Necessário refazer conexão para o próximo ciclo de postagem.
                    </div>
                  )}
                </div>
              ))}
          </div>
        )}

      </section>

      {chooseChannel && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Escolha o canal</h3>
            <p>Múltiplos canais encontrados. Selecione qual vincular à marca.</p>
            {pendingChannels.length === 0 ? (
              <p className="modal-hint">Carregando canais...</p>
            ) : (
              <div className="channel-list">
                {pendingChannels.map((ch) => (
                  <button
                    key={ch.id}
                    type="button"
                    className="channel-btn"
                    onClick={() => handleSelectChannel(ch.id, ch.title)}
                    disabled={loading}
                  >
                    {ch.title || ch.id}
                  </button>
                ))}
              </div>
            )}
            <button type="button" className="modal-close" onClick={() => { setChooseChannel(false); setPendingKey('') }}>
              Fechar
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
