export function getCurrentWeekRange(now = new Date()) {
  const start = new Date(now)
  const day = start.getDay()
  start.setDate(start.getDate() - day)
  start.setHours(0, 0, 0, 0)

  const end = new Date(start)
  end.setDate(end.getDate() + 7)

  return { start, end }
}

export function normalizeFactoryScheduleStatus(status) {
  const normalized = String(status || 'PLANNED').trim().toUpperCase()

  if (normalized === 'DONE') return 'DONE'
  if (normalized === 'POSTING') return 'POSTING'
  if (normalized === 'FAILED') return 'FAILED'
  if (normalized === 'SKIPPED') return 'SKIPPED'

  return 'PLANNED'
}

export function getFactoryWeekStatusMeta(status, { postedOnChannel = false, postedTimeLabel = '' } = {}) {
  const normalized = normalizeFactoryScheduleStatus(status)

  if (normalized === 'DONE' && postedOnChannel) {
    return {
      normalized,
      summaryKey: 'posted',
      tagClass: 'posted-channel',
      badgeLabel: postedTimeLabel ? `Postado no canal ${postedTimeLabel}` : 'Postado no canal',
      titleLabel: postedTimeLabel ? `Postado no canal às ${postedTimeLabel}` : 'Postado no canal',
    }
  }

  if (normalized === 'DONE') {
    return {
      normalized,
      summaryKey: 'posted',
      tagClass: 'posted',
      badgeLabel: 'Postado',
      titleLabel: 'Postado',
    }
  }

  if (normalized === 'FAILED') {
    return {
      normalized,
      summaryKey: 'failed',
      tagClass: 'failed',
      badgeLabel: 'Falhou',
      titleLabel: 'Falhou',
    }
  }

  if (normalized === 'SKIPPED') {
    return {
      normalized,
      summaryKey: 'skipped',
      tagClass: 'skipped',
      badgeLabel: 'Ignorado',
      titleLabel: 'Ignorado',
    }
  }

  if (normalized === 'POSTING') {
    return {
      normalized,
      summaryKey: 'pending',
      tagClass: 'posting',
      badgeLabel: 'Postando',
      titleLabel: 'Postando',
    }
  }

  return {
    normalized,
    summaryKey: 'pending',
    tagClass: 'scheduled',
    badgeLabel: 'Agendado',
    titleLabel: 'Agendado',
  }
}

export function buildFactoryWeeklyMatrix(items = [], brands = [], now = new Date()) {
  const { start, end } = getCurrentWeekRange(now)
  const filtered = (items || []).filter((item) => {
    const dt = new Date(item.scheduled_at)
    return dt >= start && dt < end
  })
  const hours = Array.from({ length: 24 }, (_, h) => h)
  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    return d
  })
  const brandNameById = (brands || []).reduce((acc, b) => {
    acc[String(b.id)] = b.name
    return acc
  }, {})
  const matrix = {}
  for (const h of hours) {
    matrix[h] = {}
    for (let d = 0; d < 7; d += 1) matrix[h][d] = []
  }

  const summaryByBrand = {}
  filtered.forEach((item) => {
    const dt = new Date(item.scheduled_at)
    const dayIndex = dt.getDay()
    const hour = dt.getHours()
    const brandName = brandNameById[String(item.brand)] || `Canal #${item.brand}`
    const postedAt = item.posted_at ? new Date(item.posted_at) : null
    const postedTimeLabel = postedAt
      ? postedAt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
      : ''
    const statusMeta = getFactoryWeekStatusMeta(item.status, {
      postedOnChannel: Boolean(item.posted_on_channel),
      postedTimeLabel,
    })

    const tag = {
      id: item.id,
      brandId: item.brand,
      brandName,
      status: statusMeta.normalized,
      statusLabel: statusMeta.badgeLabel,
      titleStatusLabel: statusMeta.titleLabel,
      tagClass: statusMeta.tagClass,
      isPosted: statusMeta.summaryKey === 'posted',
      isPostedOnChannel: statusMeta.tagClass === 'posted-channel',
      postedTimeLabel,
      externalVideoId: item.external_video_id || '',
      timeLabel: dt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
    }
    matrix[hour][dayIndex].push(tag)
    if (!summaryByBrand[item.brand]) {
      summaryByBrand[item.brand] = {
        brandId: item.brand,
        brandName,
        posted: 0,
        pending: 0,
        failed: 0,
        skipped: 0,
        total: 0,
      }
    }
    summaryByBrand[item.brand].total += 1
    summaryByBrand[item.brand][statusMeta.summaryKey] += 1
  })

  const channelSummary = Object.values(summaryByBrand).sort((a, b) => {
    if (a.pending !== b.pending) return a.pending - b.pending
    if (a.total !== b.total) return a.total - b.total
    if (a.failed !== b.failed) return a.failed - b.failed
    return a.brandName.localeCompare(b.brandName)
  })

  return { start, weekDays, hours, matrix, channelSummary }
}
