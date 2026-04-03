// Use URL direta quando o proxy falha (ex: Docker + npm no host)
const API_BASE = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE) || '/api'

function getToken() {
  return localStorage.getItem('access_token')
}

function detailToMessage(detail) {
  if (detail == null || detail === '') return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((x) => {
        if (typeof x === 'string') return x
        if (x && typeof x === 'object') {
          return Object.values(x)
            .flat()
            .filter((v) => typeof v === 'string')
            .join(' ')
        }
        return String(x)
      })
      .filter(Boolean)
      .join(' ')
  }
  if (typeof detail === 'object' && detail !== null) {
    if (typeof detail.detail === 'string') return detail.detail
    if (typeof detail.message === 'string') return detail.message
    return ''
  }
  return String(detail)
}

/**
 * Fetch com FormData: lê JSON uma vez; 401 igual ao apiRequest (logout + login).
 */
async function jsonFromMultipartFetch(res) {
  const data = await res.json().catch(() => ({}))
  if (res.status === 401) {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    window.location.href = '/login'
    throw new Error('Sessão expirada. Faça login novamente.')
  }
  if (!res.ok) {
    const msg = detailToMessage(data.detail) || data.error || `Erro ${res.status}`
    throw new Error(msg)
  }
  return data
}

export async function apiRequest(endpoint, options = {}) {
  const token = getToken()
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${API_BASE}${endpoint}`, { ...options, headers })
  if (res.status === 401) {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    window.location.href = '/login'
    throw new Error('Não autorizado')
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(data?.detail || data?.error || `Erro ${res.status}`)
  }
  return data
}

export async function login(username, password) {
  let res
  try {
    res = await fetch(`${API_BASE}/auth/token/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
  } catch (err) {
    throw new Error(
      'Não foi possível conectar ao servidor. Verifique se o Django está rodando na porta 8000 (docker compose up -d web).'
    )
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 404) {
      throw new Error(
        'Endpoint da API não encontrado (404). Verifique se o servidor correto está rodando na porta 8000.'
      )
    }
    throw new Error(data.detail || 'Usuário ou senha incorretos')
  }
  localStorage.setItem('access_token', data.access)
  localStorage.setItem('refresh_token', data.refresh)
  return data
}

export async function register(username, password, email = '') {
  const res = await fetch(`${API_BASE}/register/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, email: email || undefined }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.username?.[0] || data.password?.[0] || data.detail || 'Erro ao cadastrar')
  return data
}

export async function getBrands(factoryId = null) {
  const params = new URLSearchParams()
  if (factoryId) params.append('factory', factoryId)
  const qs = params.toString()
  return apiRequest(qs ? `/brands/?${qs}` : '/brands/')
}

export async function getFactories() {
  return apiRequest('/factories/')
}

export async function getBrand(brandId) {
  return apiRequest(`/brands/${brandId}/`)
}

export async function getFactory(factoryId) {
  return apiRequest(`/factories/${factoryId}/`)
}

export async function updateFactory(factoryId, payload) {
  return apiRequest(`/factories/${factoryId}/`, {
    method: 'PATCH',
    body: JSON.stringify(payload || {}),
  })
}

export async function triggerImmediateSchedule(factoryId, targetDate = null, brandId = null) {
  const body = {}
  if (targetDate) body.target_date = targetDate
  if (brandId) body.brand_id = brandId
  return apiRequest(`/factories/${factoryId}/trigger-immediate-schedule/`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function triggerBrandImmediateSchedule(brandId, targetDate = null) {
  const body = targetDate ? { target_date: targetDate } : {}
  return apiRequest(`/brands/${brandId}/trigger-immediate-schedule/`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function getFactorySchedules(factoryId, status = null, brandId = null) {
  if (!factoryId) return []
  const params = new URLSearchParams()
  params.append('factory', factoryId)
  if (status) params.append('status', status)
  if (brandId) params.append('brand', brandId)
  return apiRequest(`/factory-schedules/?${params.toString()}`)
}

export async function getSearchChannels(factoryId = null) {
  const qs = factoryId ? `?factory=${factoryId}` : ''
  return apiRequest(`/search-channels/${qs}`)
}

export async function createSearchChannel(data) {
  return apiRequest('/search-channels/', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateSearchChannel(id, data) {
  return apiRequest(`/search-channels/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deleteSearchChannel(id) {
  return apiRequest(`/search-channels/${id}/`, { method: 'DELETE' })
}

export async function getFactoryYoutubeCheckConnectUrl(factoryId) {
  const data = await apiRequest(`/factories/${factoryId}/youtube-check-connect-url/`)
  return data.url
}

export async function getVideoInventory({
  factoryId = null,
  brandId = null,
  status = null,
  videoType = null,
} = {}) {
  const params = new URLSearchParams()
  if (factoryId) params.append('factory', factoryId)
  if (brandId) params.append('brand', brandId)
  if (status) params.append('status', status)
  if (videoType) params.append('video_type', videoType)
  const qs = params.toString()
  return apiRequest(qs ? `/video-inventory/?${qs}` : '/video-inventory/')
}

export async function removeAwaitingInventoryItem(id) {
  return apiRequest(`/video-inventory/${id}/remove-awaiting/`, {
    method: 'POST',
  })
}

export async function retryAwaitingInventoryItem(id, payload = {}) {
  const hasBody = payload && (payload.scheduled_at != null && payload.scheduled_at !== '')
  return apiRequest(`/video-inventory/${id}/retry-posting/`, {
    method: 'POST',
    ...(hasBody ? { body: JSON.stringify({ scheduled_at: payload.scheduled_at }) } : {}),
  })
}

export async function downloadInventoryMedia(id, title = '') {
  const token = getToken()
  const res = await fetch(`${API_BASE}/video-inventory/${id}/download-media/`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.error || `Erro ${res.status}`)
  }
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition')
  let filename = (title || `video_${id}`).replace(/[/\\:*?"<>|]/g, '').trim() || `video_${id}`
  if (!filename.toLowerCase().endsWith('.zip')) filename = `${filename}_midias.zip`
  if (disposition) {
    const match = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?/i) || disposition.match(/filename=["']?([^"';]+)["']?/i)
    if (match) filename = match[1].trim()
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export async function markInventoryPosted(id, payload = {}) {
  const hasBody = payload && (payload.posted_at != null && payload.posted_at !== '')
  return apiRequest(`/video-inventory/${id}/mark-posted/`, {
    method: 'POST',
    ...(hasBody ? { body: JSON.stringify({ posted_at: payload.posted_at }) } : {}),
  })
}

export async function createBrand(nameOrPayload) {
  const payload = typeof nameOrPayload === 'string'
    ? { name: nameOrPayload.trim() }
    : { ...(nameOrPayload || {}) }
  return apiRequest('/brands/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteBrand(id) {
  const token = getToken()
  const res = await fetch(`${API_BASE}/brands/${id}/`, {
    method: 'DELETE',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.detail || data.error || `Erro ${res.status}`)
}

export async function updateBrandYoutubeDescription(brandId, youtubeDescriptionExtra, youtubeMadeForKids) {
  const body = { youtube_description_extra: youtubeDescriptionExtra ?? '' }
  if (youtubeMadeForKids !== undefined) body.youtube_made_for_kids = !!youtubeMadeForKids
  return apiRequest(`/brands/${brandId}/youtube-description/`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function updateBrand(brandId, payload) {
  return apiRequest(`/brands/${brandId}/`, {
    method: 'PATCH',
    body: JSON.stringify(payload || {}),
  })
}

export async function getBrandAssets(brandId, assetType) {
  let url = '/brand-assets/'
  const params = new URLSearchParams()
  if (brandId) params.append('brand', brandId)
  if (assetType) params.append('asset_type', assetType)
  if (params.toString()) url += '?' + params.toString()
  return apiRequest(url)
}

export async function createBrandAsset(brandId, assetType, file, label = '') {
  const formData = new FormData()
  formData.append('brand', brandId)
  formData.append('asset_type', assetType)
  formData.append('file', file)
  if (label) formData.append('label', label)
  const token = getToken()
  const res = await fetch(`${API_BASE}/brand-assets/`, {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || err.file || err.asset_type || `Erro ${res.status}`)
  }
  return res.json()
}

export async function deleteBrandAsset(id) {
  const token = getToken()
  const res = await fetch(`${API_BASE}/brand-assets/${id}/`, {
    method: 'DELETE',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.detail || data.error || `Erro ${res.status}`)
}

export async function uploadSource(brandId, title, file) {
  const formData = new FormData()
  formData.append('brand', brandId)
  formData.append('title', title)
  formData.append('file', file)
  const token = getToken()
  const res = await fetch(`${API_BASE}/sources/`, {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || err.file || err.error || `Erro ${res.status}`)
  }
  return res.json()
}

export function uploadSourceWithProgress(brandId, title, file, onProgress) {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    formData.append('brand', brandId)
    formData.append('title', title)
    formData.append('file', file)
    const token = getToken()
    const xhr = new XMLHttpRequest()
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    })
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch {
          resolve({})
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText)
          reject(new Error(err.detail || err.file || err.error || `Erro ${xhr.status}`))
        } catch {
          reject(new Error(`Erro ${xhr.status}`))
        }
      }
    })
    xhr.addEventListener('error', () => reject(new Error('Erro de rede')))
    xhr.open('POST', `${API_BASE}/sources/`)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.send(formData)
  })
}

export async function getSources(brandId = null) {
  const url = brandId ? `/sources/?brand=${brandId}` : '/sources/'
  return apiRequest(url)
}

export async function createCuts(sourceId, cuts) {
  return apiRequest('/cuts/', {
    method: 'POST',
    body: JSON.stringify({ source: sourceId, cuts }),
  })
}

export async function extractCuts(sourceId, cuts) {
  return apiRequest(`/sources/${sourceId}/extract_cuts/`, {
    method: 'POST',
    body: JSON.stringify({ cuts }),
  })
}

export async function getCuts(sourceId = null, brandId = null) {
  const params = new URLSearchParams()
  if (sourceId) params.append('source', sourceId)
  if (brandId) params.append('brand', brandId)
  const qs = params.toString()
  return apiRequest(qs ? `/cuts/?${qs}` : '/cuts/')
}

export async function deleteCut(id) {
  const res = await fetch(`${API_BASE}/cuts/${id}/`, {
    method: 'DELETE',
    headers: getToken() ? { 'Authorization': `Bearer ${getToken()}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.error || data.detail || `Erro ${res.status}`)
}

export async function uploadCut(file, name = '', format = '', brandId = null) {
  const formData = new FormData()
  formData.append('file', file)
  if (name) formData.append('name', name)
  if (format) formData.append('format', format)
  if (brandId) formData.append('brand', brandId)
  const token = getToken()
  const res = await fetch(`${API_BASE}/cuts/upload/`, {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })
  return jsonFromMultipartFetch(res)
}

export async function uploadJob(file, name = '', format = '', brandId = null) {
  const formData = new FormData()
  formData.append('file', file)
  if (name) formData.append('name', name)
  if (format) formData.append('format', format)
  if (brandId) formData.append('brand', brandId)
  const token = getToken()
  const res = await fetch(`${API_BASE}/jobs/upload/`, {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })
  return jsonFromMultipartFetch(res)
}

export async function createJob(data, brandId = null) {
  const body = brandId ? { ...data, brand: parseInt(brandId, 10) } : data
  return apiRequest('/jobs/', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function getJobs(archived = false, brandId = null) {
  const params = new URLSearchParams()
  params.append('archived', archived ? 'true' : 'false')
  if (brandId) params.append('brand', brandId)
  return apiRequest(`/jobs/?${params.toString()}`)
}

export async function archiveJob(id) {
  return apiRequest(`/jobs/${id}/archive/`, { method: 'POST' })
}

export async function deleteJob(id) {
  const token = getToken()
  const res = await fetch(`${API_BASE}/jobs/${id}/`, {
    method: 'DELETE',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
  })
  if (res.status === 401) {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    window.location.href = '/login'
    throw new Error('Sessão expirada. Faça login novamente.')
  }
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(detailToMessage(data.detail) || data.error || `Erro ${res.status}`)
}

export async function getJob(id) {
  return apiRequest(`/jobs/${id}/`)
}

export async function runJob(id) {
  return apiRequest(`/jobs/${id}/run/`, { method: 'POST' })
}

export async function generateSubtitles(jobId) {
  return apiRequest(`/jobs/${jobId}/generate-subtitles/`, { method: 'POST' })
}

export async function updateSubtitles(jobId, segments, style) {
  const body = {}
  if (segments != null) body.segments = segments
  if (style != null) body.style = style
  return apiRequest(`/jobs/${jobId}/subtitles/`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function burnSubtitles(jobId) {
  return apiRequest(`/jobs/${jobId}/burn-subtitles/`, { method: 'POST' })
}

export async function downloadJobVideo(jobId, jobName) {
  const token = getToken()
  const res = await fetch(`${API_BASE}/jobs/${jobId}/download/`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error('Erro ao baixar')
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition')
  let filename = (jobName || `Job ${jobId}`).replace(/[/\\:*?"<>|]/g, '').trim() || `job_${jobId}`
  if (!filename.toLowerCase().endsWith('.mp4')) filename += '.mp4'
  if (disposition) {
    const match = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?/i) || disposition.match(/filename=["']?([^"';]+)["']?/i)
    if (match) filename = match[1].trim()
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export async function getScheduledPosts({ brandId = null, factoryId = null } = {}) {
  const params = new URLSearchParams()
  if (brandId) params.append('brand', brandId)
  if (factoryId) params.append('factory', factoryId)
  const qs = params.toString()
  const url = qs ? `/scheduled-posts/?${qs}` : '/scheduled-posts/'
  return apiRequest(url)
}

export async function createScheduledPost({
  jobId,
  platforms,
  scheduledAt,
  socialAccountId = null,
  title = '',
  description = '',
  tags = [],
  privacyStatus = 'private',
}) {
  const body = { job: jobId, platforms, scheduled_at: scheduledAt }
  if (socialAccountId) body.social_account = socialAccountId
  if (title) body.title = title
  if (description) body.description = description
  if (tags?.length) body.tags = Array.isArray(tags) ? tags : tags.split(',').map((t) => t.trim()).filter(Boolean)
  if (privacyStatus) body.privacy_status = privacyStatus
  return apiRequest('/scheduled-posts/', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function rescheduleScheduledPost(id, scheduledAt) {
  return apiRequest(`/scheduled-posts/${id}/reschedule/`, {
    method: 'POST',
    body: JSON.stringify({ scheduled_at: scheduledAt }),
  })
}

export async function deleteScheduledPost(id) {
  const res = await fetch(`${API_BASE}/scheduled-posts/${id}/`, {
    method: 'DELETE',
    headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.detail || data.error || `Erro ${res.status}`)
}

export async function removeAwaitingScheduledPost(id) {
  return apiRequest(`/scheduled-posts/${id}/remove-awaiting/`, {
    method: 'POST',
  })
}

// Contas sociais
export async function getSocialAccounts(brandId) {
  const url = brandId ? `/social-accounts/?brand=${brandId}` : '/social-accounts/'
  return apiRequest(url)
}

export async function getBrandSocialAccounts(brandId) {
  return apiRequest(`/brands/${brandId}/social_accounts/`)
}

export async function getYoutubeConnectUrl(brandId) {
  const data = await apiRequest(`/brands/${brandId}/youtube_connect_url/`)
  return data.url
}

export async function getYoutubeConnectUrlForCredential(brandId, youtubeCredentialId = null) {
  const params = new URLSearchParams()
  if (youtubeCredentialId) params.append('youtube_credential_id', String(youtubeCredentialId))
  const qs = params.toString()
  const data = await apiRequest(`/brands/${brandId}/youtube_connect_url/${qs ? `?${qs}` : ''}`)
  return data.url
}

export async function getBrandYoutubeCredentials(brandId) {
  const params = new URLSearchParams()
  if (brandId) params.append('brand', String(brandId))
  const qs = params.toString()
  return apiRequest(`/brand-youtube-credentials/${qs ? `?${qs}` : ''}`)
}

export async function createBrandYoutubeCredential(payload) {
  return apiRequest('/brand-youtube-credentials/', {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  })
}

export async function updateBrandYoutubeCredential(id, payload) {
  return apiRequest(`/brand-youtube-credentials/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(payload || {}),
  })
}

export async function deleteBrandYoutubeCredential(id) {
  const res = await fetch(`${API_BASE}/brand-youtube-credentials/${id}/`, {
    method: 'DELETE',
    headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.detail || data.error || `Erro ${res.status}`)
}

export async function getYoutubePendingChannels(key) {
  return apiRequest(`/youtube/pending-channels/?key=${key}`)
}

export async function youtubeSelectChannel(channelId, channelTitle = '', key) {
  const body = { channel_id: channelId, channel_title: channelTitle }
  if (key) body.key = key
  return apiRequest('/youtube/select-channel/', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function deleteSocialAccount(id) {
  const res = await fetch(`${API_BASE}/social-accounts/${id}/`, {
    method: 'DELETE',
    headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.detail || data.error || `Erro ${res.status}`)
}

// Cortes Automáticos
export async function getAutoCutAnalyses(brandId = null, { excludeFinalized = false } = {}) {
  const params = new URLSearchParams()
  if (brandId) params.append('brand', brandId)
  if (excludeFinalized) params.append('exclude_finalized', '1')
  const qs = params.toString()
  return apiRequest(`/auto-cuts/${qs ? '?' + qs : ''}`)
}

export async function getAutoCutAnalysis(id) {
  return apiRequest(`/auto-cuts/${id}/`)
}

export async function resetStuckAutoCuts(brandId) {
  const url = brandId ? `/auto-cuts/reset-stuck/?brand=${brandId}` : '/auto-cuts/reset-stuck/'
  return apiRequest(url, { method: 'POST' })
}

export async function deleteStuckAutoCuts(brandId) {
  const url = brandId ? `/auto-cuts/delete-stuck/?brand=${brandId}` : '/auto-cuts/delete-stuck/'
  return apiRequest(url, { method: 'POST' })
}

export async function createReadyCutsAnalysis({
  files,
  brandId,
  name,
  verticalMode = 'zoom_crop',
  transcribe = true,
  createLongVideo = false,
  titlesLanguage = 'pt',
  longOverlayEnabled = false,
  longOverlayAssetId = null,
}) {
  const formData = new FormData()
  if (files?.length) {
    for (const f of files) formData.append('files', f)
  } else if (files?.[0]) {
    formData.append('file', files[0])
  }
  if (brandId) formData.append('brand', brandId)
  if (name != null && String(name).trim()) formData.append('name', String(name).trim())
  formData.append('vertical_mode', verticalMode || 'zoom_crop')
  formData.append('transcribe', transcribe ? 'true' : 'false')
  formData.append('create_long_video', createLongVideo ? 'true' : 'false')
  const tl = String(titlesLanguage || 'pt').toLowerCase() === 'en' ? 'en' : 'pt'
  formData.append('titles_language', tl)
  formData.append('long_overlay_enabled', longOverlayEnabled ? 'true' : 'false')
  if (longOverlayEnabled && longOverlayAssetId) {
    formData.append('long_overlay_asset', String(longOverlayAssetId))
  }
  const token = getToken()
  const res = await fetch(`${API_BASE}/auto-cuts/upload-ready-cuts/`, {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })
  return jsonFromMultipartFetch(res)
}

export async function createAutoCutAnalysis({
  file,
  sourceId,
  youtubeUrl,
  brandId,
  targetBrandId,
  distributionMode = 'theme',
  name,
  assunto,
  convidados,
  promptVersion,
  thumbnailFont,
  thumbnailBandColor,
  thumbnailTextColor,
  thumbnailStrokeColor,
  shortsTarget,
  longsTarget,
  verticalMode = 'zoom_crop',
  longOverlayEnabled = false,
  longOverlayAssetId = null,
}) {
  const formData = new FormData()
  if (file) formData.append('file', file)
  if (sourceId) formData.append('source', sourceId)
  if (youtubeUrl) formData.append('youtube_url', youtubeUrl)
  if (brandId) formData.append('brand', brandId)
  if (targetBrandId) formData.append('target_brand', targetBrandId)
  formData.append('distribution_mode', distributionMode || 'theme')
  formData.append('vertical_mode', verticalMode || 'zoom_crop')
  if (name) formData.append('name', name)
  if (assunto) formData.append('assunto', assunto)
  if (convidados) formData.append('convidados', convidados)
  if (promptVersion) formData.append('prompt_version', promptVersion)
  if (thumbnailFont) formData.append('thumbnail_font', thumbnailFont)
  if (thumbnailBandColor) formData.append('thumbnail_band_color', thumbnailBandColor)
  if (thumbnailTextColor) formData.append('thumbnail_text_color', thumbnailTextColor)
  if (thumbnailStrokeColor) formData.append('thumbnail_stroke_color', thumbnailStrokeColor)
  if (shortsTarget != null) formData.append('shorts_target', String(shortsTarget))
  if (longsTarget != null) formData.append('longs_target', String(longsTarget))
  formData.append('long_overlay_enabled', longOverlayEnabled ? 'true' : 'false')
  if (longOverlayEnabled && longOverlayAssetId) {
    formData.append('long_overlay_asset', String(longOverlayAssetId))
  }
  const token = getToken()
  const res = await fetch(`${API_BASE}/auto-cuts/`, {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })
  return jsonFromMultipartFetch(res)
}

export async function deleteAutoCutSuggestion(id) {
  const res = await fetch(`${API_BASE}/auto-cut-suggestions/${id}/`, {
    method: 'DELETE',
    headers: getToken() ? { 'Authorization': `Bearer ${getToken()}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.error || data.detail || `Erro ${res.status}`)
}

export async function createCutFromSuggestion(id) {
  return apiRequest(`/auto-cut-suggestions/${id}/create-cut/`, { method: 'POST' })
}

export async function updateAutoCutCorte(id, { needs_subtitle, user_wants_finalize, title, subtitle_segments }) {
  const body = {}
  if (needs_subtitle !== undefined) body.needs_subtitle = needs_subtitle
  if (user_wants_finalize !== undefined) body.user_wants_finalize = user_wants_finalize
  if (title !== undefined) body.title = title
  if (subtitle_segments !== undefined) body.subtitle_segments = subtitle_segments
  return apiRequest(`/auto-cut-cortes/${id}/`, { method: 'PATCH', body: JSON.stringify(body) })
}

export async function finalizarAutoCutJob(analysisId, {
  subtitle_style: subtitleStyle = {},
  vertical_mode: verticalMode = 'zoom_crop',
  background_color: backgroundColor = '#000000',
  custom_text: customText = '',
  font_size_title: fontSizeTitle,
  font_size_text: fontSizeText,
  title_color: titleColor,
  text_color: textColor,
  horizontal_insert_logo: horizontalInsertLogo = false,
  horizontal_logo_x: horizontalLogoX,
  horizontal_logo_y: horizontalLogoY,
  overlay_animation_asset_id: overlayAnimationAssetId,
  overlay_position: overlayPosition = 'bottom_right',
  overlay_margin: overlayMargin,
  overlay_height: overlayHeight,
  long_overlay_enabled: longOverlayEnabled,
  long_overlay_asset_id: longOverlayAssetId,
} = {}) {
  const body = {
    subtitle_style: subtitleStyle,
    vertical_mode: verticalMode,
    background_color: backgroundColor,
    custom_text: customText,
    horizontal_insert_logo: horizontalInsertLogo,
  }
  if (fontSizeTitle != null) body.font_size_title = fontSizeTitle
  if (fontSizeText != null) body.font_size_text = fontSizeText
  if (titleColor != null) body.title_color = titleColor
  if (textColor != null) body.text_color = textColor
  if (horizontalLogoX != null) body.horizontal_logo_x = horizontalLogoX
  if (horizontalLogoY != null) body.horizontal_logo_y = horizontalLogoY
  if (overlayAnimationAssetId != null) body.overlay_animation_asset_id = overlayAnimationAssetId
  if (overlayPosition != null) body.overlay_position = overlayPosition
  if (overlayMargin != null) body.overlay_margin = overlayMargin
  if (overlayHeight != null) body.overlay_height = overlayHeight
  if (longOverlayEnabled !== undefined) body.long_overlay_enabled = longOverlayEnabled
  if (longOverlayAssetId !== undefined) body.long_overlay_asset_id = longOverlayAssetId
  return apiRequest(`/auto-cuts/${analysisId}/finalizar/`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function deleteAutoCutAnalysis(analysisId) {
  const res = await fetch(`${API_BASE}/auto-cuts/${analysisId}/`, {
    method: 'DELETE',
    headers: getToken() ? { 'Authorization': `Bearer ${getToken()}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.error || data.detail || `Erro ${res.status}`)
}

export async function bulkScheduleAutoCutAnalysis(
  analysisId,
  { startAt, endAt, privacyStatus = 'private', socialAccountId = null },
) {
  return apiRequest(`/auto-cuts/${analysisId}/bulk-schedule/`, {
    method: 'POST',
    body: JSON.stringify({
      start_at: startAt,
      end_at: endAt,
      privacy_status: privacyStatus,
      social_account: socialAccountId,
    }),
  })
}

export async function scheduleAutoCutCorte(corteId, { scheduledAt, privacyStatus = 'private' }) {
  return apiRequest(`/auto-cut-cortes/${corteId}/schedule/`, {
    method: 'POST',
    body: JSON.stringify({
      scheduled_at: scheduledAt,
      privacy_status: privacyStatus,
    }),
  })
}

export async function getAutoCutCortes(brandId, { finalized, date_from, date_to, format } = {}) {
  const params = new URLSearchParams()
  if (brandId) params.append('brand', brandId)
  if (finalized) params.append('finalized', '1')
  if (date_from) params.append('date_from', date_from)
  if (date_to) params.append('date_to', date_to)
  if (format) params.append('format', format)
  const qs = params.toString()
  return apiRequest(`/auto-cut-cortes/${qs ? '?' + qs : ''}`)
}

export async function deleteAutoCutCorte(id, brandId) {
  const url = brandId ? `${API_BASE}/auto-cut-cortes/${id}/?brand=${brandId}` : `${API_BASE}/auto-cut-cortes/${id}/`
  const res = await fetch(url, {
    method: 'DELETE',
    headers: getToken() ? { 'Authorization': `Bearer ${getToken()}` } : {},
  })
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.error || data.detail || `Erro ${res.status}`)
}

export async function uploadAutoCutCorteThumbnail(corteId, file) {
  const formData = new FormData()
  formData.append('thumbnail', file)
  const token = getToken()
  const res = await fetch(`${API_BASE}/auto-cut-cortes/${corteId}/`, {
    method: 'PATCH',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.error || data.detail || data.thumbnail?.[0] || `Erro ${res.status}`)
  }
  return data
}
