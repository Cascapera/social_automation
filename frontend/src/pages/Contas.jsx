import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useBrand } from '../context/BrandContext'
import {
  getBrandSocialAccounts,
  getYoutubeConnectUrl,
  getYoutubePendingChannels,
  youtubeSelectChannel,
  deleteSocialAccount,
} from '../api'
import './Contas.css'

const PLATFORM_LABELS = {
  IG: 'Instagram',
  TT: 'TikTok',
  YT: 'YouTube Shorts',
  YTB: 'YouTube',
}

export default function Contas() {
  const { brandId } = useBrand()
  const [searchParams] = useSearchParams()
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [oauthPending, setOauthPending] = useState(null)
  const [chooseChannel, setChooseChannel] = useState(null)
  const [pendingKey, setPendingKey] = useState('')
  const [pendingChannels, setPendingChannels] = useState([])

  const urlError = searchParams.get('error')
  const urlDetail = searchParams.get('detail')
  const youtubeConnected = searchParams.get('youtube_connected')
  const youtubeChooseChannel = searchParams.get('youtube_choose_channel')

  useEffect(() => {
    if (brandId) {
      getBrandSocialAccounts(brandId)
        .then(setAccounts)
        .catch(() => setAccounts([]))
    } else {
      setAccounts([])
    }
  }, [brandId])

  useEffect(() => {
    if (urlError) {
      const msg = urlDetail ? `${urlError}: ${urlDetail}` : urlError
      setError(msg)
    }
    if (youtubeConnected) {
      setError('')
      if (brandId) getBrandSocialAccounts(brandId).then(setAccounts)
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

  async function handleConnectYouTube() {
    if (!brandId) {
      setError('Selecione uma marca')
      return
    }
    setError('')
    setLoading(true)
    try {
      const url = await getYoutubeConnectUrl(brandId)
      window.location.href = url
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  async function handleSelectChannel(channelId, channelTitle) {
    setLoading(true)
    setError('')
    try {
      await youtubeSelectChannel(channelId, channelTitle, pendingKey)
      setChooseChannel(false)
      setPendingKey('')
      if (brandId) getBrandSocialAccounts(brandId).then(setAccounts)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleDisconnect(id) {
    if (!confirm('Desconectar esta conta?')) return
    try {
      await deleteSocialAccount(id)
      if (brandId) getBrandSocialAccounts(brandId).then(setAccounts)
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

  const youtubeAccounts = accounts.filter((a) => a.platform === 'YTB' || a.platform === 'YT')

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
        <button
          type="button"
          className="btn-connect"
          onClick={handleConnectYouTube}
          disabled={loading}
        >
          {loading ? 'Conectando...' : 'Conectar YouTube'}
        </button>

        {youtubeAccounts.length > 0 && (
          <div className="accounts-list">
            <h3>Canais conectados</h3>
            {youtubeAccounts.map((acc) => (
              <div key={acc.id} className="account-card">
                <span className="account-name">{acc.account_name || acc.channel_id || 'Canal'}</span>
                <span className="account-platform">{PLATFORM_LABELS[acc.platform] || acc.platform}</span>
                <button
                  type="button"
                  className="btn-disconnect"
                  onClick={() => handleDisconnect(acc.id)}
                >
                  Desconectar
                </button>
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
