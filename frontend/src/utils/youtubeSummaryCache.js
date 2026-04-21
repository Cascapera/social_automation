/**
 * Cache local do resumo YouTube (Upload Post) por factory + período + escopo de brand.
 * Reduz chamadas à API (pesada / rate limit) e permite leitura offline do último snapshot.
 */

const STORAGE_PREFIX = 'yt_factory_summary_v1'

export const YOUTUBE_SUMMARY_CACHE_TTL_MS = 5 * 60 * 1000

function storageKey(factoryId, period, brandId = null) {
  return `${STORAGE_PREFIX}:${factoryId}:${period}:${brandId || 'all'}`
}

function getStorage() {
  if (typeof localStorage === 'undefined') return null
  return localStorage
}

/**
 * @param {number|string} factoryId
 * @param {string} period
 * @param {number|string|null} [brandId]
 * @returns {{ data: unknown, fetchedAt: number } | null}
 */
export function loadYoutubeSummaryCache(factoryId, period, brandId = null) {
  const store = getStorage()
  if (!store) return null
  try {
    const raw = store.getItem(storageKey(factoryId, period, brandId))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (
      !parsed ||
      typeof parsed.fetchedAt !== 'number' ||
      parsed.data === undefined
    ) {
      return null
    }
    return { data: parsed.data, fetchedAt: parsed.fetchedAt }
  } catch {
    return null
  }
}

/**
 * @param {number|string} factoryId
 * @param {string} period
 * @param {unknown} data
 * @param {number|string|null} [brandId]
 */
export function saveYoutubeSummaryCache(factoryId, period, data, brandId = null) {
  const store = getStorage()
  if (!store) return
  try {
    const payload = JSON.stringify({ fetchedAt: Date.now(), data })
    store.setItem(storageKey(factoryId, period, brandId), payload)
  } catch {
    // quota / private mode — ignora silenciosamente
  }
}

/**
 * @param {{ fetchedAt: number } | null | undefined} entry
 * @param {number} [now]
 */
export function isYoutubeSummaryCacheFresh(entry, now = Date.now()) {
  if (!entry || typeof entry.fetchedAt !== 'number') return false
  return now - entry.fetchedAt < YOUTUBE_SUMMARY_CACHE_TTL_MS
}
