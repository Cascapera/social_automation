import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import {
  loadYoutubeSummaryCache,
  saveYoutubeSummaryCache,
  isYoutubeSummaryCacheFresh,
  YOUTUBE_SUMMARY_CACHE_TTL_MS,
} from './youtubeSummaryCache.js'

let mem = {}

beforeEach(() => {
  mem = {}
  globalThis.localStorage = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null),
    setItem: (k, v) => {
      mem[k] = String(v)
    },
    removeItem: (k) => {
      delete mem[k]
    },
    clear: () => {
      mem = {}
    },
  }
})

test('save e load devolvem os mesmos dados', () => {
  const data = { brands: [{ id: 1 }], period: 'last_month' }
  saveYoutubeSummaryCache(42, 'last_month', data)
  const entry = loadYoutubeSummaryCache(42, 'last_month')
  assert.ok(entry)
  assert.deepEqual(entry.data, data)
  assert.equal(typeof entry.fetchedAt, 'number')
})

test('cache fresco dentro do TTL', () => {
  const now = 1_000_000
  assert.equal(isYoutubeSummaryCacheFresh({ fetchedAt: now }, now + 1000), true)
  assert.equal(
    isYoutubeSummaryCacheFresh({ fetchedAt: now }, now + YOUTUBE_SUMMARY_CACHE_TTL_MS - 1),
    true,
  )
})

test('cache expirado fora do TTL', () => {
  const now = 1_000_000
  assert.equal(
    isYoutubeSummaryCacheFresh({ fetchedAt: now }, now + YOUTUBE_SUMMARY_CACHE_TTL_MS),
    false,
  )
})

test('load devolve null para JSON inválido', () => {
  mem['yt_factory_summary_v1:1:last_month'] = 'not-json{'
  assert.equal(loadYoutubeSummaryCache(1, 'last_month'), null)
})

test('load devolve null sem entrada', () => {
  assert.equal(loadYoutubeSummaryCache(99, 'last_week'), null)
})
