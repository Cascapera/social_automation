/**
 * Rótulo de uma brand na prévia do Postar Imediato.
 *
 * Existe fora do componente porque "zero vídeo" tem três causas diferentes e o usuário
 * precisa saber qual é antes de decidir: o dia já foi agendado, o banco está vazio, ou não
 * sobrou horário elegível (todos os slots do dia já passaram). Mostrar só "0" faria o botão
 * parecer quebrado nos três casos.
 */
export function describeBrandPreview(brand) {
  const videos = brand?.videos || 0
  if (videos > 0) {
    return `${videos} vídeo${videos > 1 ? 's' : ''}`
  }
  if ((brand?.slots_already_scheduled || 0) > 0) {
    return 'dia já agendado'
  }
  if ((brand?.slots_without_stock || 0) > 0) {
    return 'sem vídeo no banco'
  }
  return 'nenhum horário disponível'
}

/** Texto do botão de confirmação. `null` enquanto a prévia não chegou. */
export function describePostButton(preview, { loading = false, posting = false } = {}) {
  if (posting) return 'Publicando...'
  if (loading || !preview) return 'Calculando...'
  const total = preview.total || 0
  if (total < 1) return 'Nada para postar'
  return `Postar ${total} vídeo${total > 1 ? 's' : ''}`
}
