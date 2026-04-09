/**
 * Cache local do resumo YouTube (Upload Post) por factory + período.
 * Reduz chamadas à API (pesada / rate limit) e permite leitura offline do último snapshot.
 */

const STORAGE_PREFIX = 'yt_factory_summary_v1'

export const YOUTUBE_SUMMARY_CACHE_TTL_MS = 5 * 60 * 1000

function storageKey(factoryId, period) {
  return `${STORAGE_PREFIX}:${factoryId}:${period}`
}

function getStorage() {
  if (typeof localStorage === 'undefined') return null
  return localStorage
}

/**
 * @param {number|string} factoryId
 * @param {string} period
 * @returns {{ data: unknown, fetchedAt: number } | null}
 */
export function loadYoutubeSummaryCache(factoryId, period) {
  const store = getStorage()
  if (!store) return null
  try {
    const raw = store.getItem(storageKey(factoryId, period))
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
 */
export function saveYoutubeSummaryCache(factoryId, period, data) {
  const store = getStorage()
  if (!store) return
  try {
    const payload = JSON.stringify({ fetchedAt: Date.now(), data })
    store.setItem(storageKey(factoryId, period), payload)
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
