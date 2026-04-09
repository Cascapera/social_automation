import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  buildFactoryWeeklyMatrix,
  getFactoryWeekStatusMeta,
} from './factoryWeeklySchedule.js'

test('resumo semanal conta apenas PLANNED e POSTING como pendentes reais', () => {
  const now = new Date('2026-04-09T12:00:00')
  const items = [
    { id: 1, brand: 7, scheduled_at: '2026-04-08T06:46:00', status: 'PLANNED' },
    { id: 2, brand: 7, scheduled_at: '2026-04-08T18:11:00', status: 'POSTING' },
    { id: 3, brand: 7, scheduled_at: '2026-04-08T17:20:00', status: 'FAILED' },
    { id: 4, brand: 7, scheduled_at: '2026-04-08T17:21:00', status: 'SKIPPED' },
    {
      id: 5,
      brand: 7,
      scheduled_at: '2026-04-08T18:46:00',
      status: 'DONE',
      posted_on_channel: true,
      posted_at: '2026-04-08T19:05:00',
    },
  ]

  const result = buildFactoryWeeklyMatrix(items, [{ id: 7, name: 'Only Crazy' }], now)

  assert.equal(result.channelSummary.length, 1)
  assert.deepEqual(result.channelSummary[0], {
    brandId: 7,
    brandName: 'Only Crazy',
    posted: 1,
    pending: 2,
    failed: 1,
    skipped: 1,
    total: 5,
  })
})

test('status meta diferencia falha, ignorado e postagem confirmada', () => {
  assert.deepEqual(getFactoryWeekStatusMeta('FAILED'), {
    normalized: 'FAILED',
    summaryKey: 'failed',
    tagClass: 'failed',
    badgeLabel: 'Falhou',
    titleLabel: 'Falhou',
  })

  assert.deepEqual(getFactoryWeekStatusMeta('SKIPPED'), {
    normalized: 'SKIPPED',
    summaryKey: 'skipped',
    tagClass: 'skipped',
    badgeLabel: 'Ignorado',
    titleLabel: 'Ignorado',
  })

  assert.deepEqual(
    getFactoryWeekStatusMeta('DONE', { postedOnChannel: true, postedTimeLabel: '18:11' }),
    {
      normalized: 'DONE',
      summaryKey: 'posted',
      tagClass: 'posted-channel',
      badgeLabel: 'Postado no canal 18:11',
      titleLabel: 'Postado no canal às 18:11',
    },
  )
})
