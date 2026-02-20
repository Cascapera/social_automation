const API_BASE = '/api'

function getToken() {
  return localStorage.getItem('access_token')
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
  const data = res.ok ? await res.json().catch(() => null) : null
  if (!res.ok) {
    throw new Error(data?.detail || data?.error || JSON.stringify(data) || `Erro ${res.status}`)
  }
  return data
}

export async function login(username, password) {
  const res = await fetch(`${API_BASE}/auth/token/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || 'Usuário ou senha incorretos')
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

export async function getBrands() {
  return apiRequest('/brands/')
}

export async function createBrand(name) {
  return apiRequest('/brands/', {
    method: 'POST',
    body: JSON.stringify({ name: name.trim() }),
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || err.detail || `Erro ${res.status}`)
  }
  return res.json()
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || err.detail || `Erro ${res.status}`)
  }
  return res.json()
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
  if (res.status === 204) return
  const data = await res.json().catch(() => ({}))
  throw new Error(data.detail || data.error || `Erro ${res.status}`)
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

export async function getScheduledPosts(brandId = null) {
  const url = brandId ? `/scheduled-posts/?brand=${brandId}` : '/scheduled-posts/'
  return apiRequest(url)
}

export async function createScheduledPost(jobId, platforms, scheduledAt) {
  return apiRequest('/scheduled-posts/', {
    method: 'POST',
    body: JSON.stringify({ job: jobId, platforms, scheduled_at: scheduledAt }),
  })
}
