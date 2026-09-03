import test from 'node:test'
import assert from 'node:assert/strict'

import { describeBrandPreview, describePostButton } from './immediatePostPreview.js'

test('brand com vídeos mostra a contagem, no plural certo', () => {
  assert.equal(describeBrandPreview({ videos: 1 }), '1 vídeo')
  assert.equal(describeBrandPreview({ videos: 3 }), '3 vídeos')
})

test('zero vídeo distingue as três causas', () => {
  assert.equal(
    describeBrandPreview({ videos: 0, slots_already_scheduled: 2, slots_without_stock: 0 }),
    'dia já agendado',
  )
  assert.equal(
    describeBrandPreview({ videos: 0, slots_already_scheduled: 0, slots_without_stock: 2 }),
    'sem vídeo no banco',
  )
  assert.equal(
    describeBrandPreview({ videos: 0, slots_already_scheduled: 0, slots_without_stock: 0 }),
    'nenhum horário disponível',
  )
})

test('dia agendado tem precedência sobre falta de estoque', () => {
  // As duas causas podem coexistir; a primeira explica melhor por que o botão não age.
  assert.equal(
    describeBrandPreview({ videos: 0, slots_already_scheduled: 1, slots_without_stock: 1 }),
    'dia já agendado',
  )
})

test('brand indefinida não quebra a tela', () => {
  assert.equal(describeBrandPreview(undefined), 'nenhum horário disponível')
  assert.equal(describeBrandPreview({}), 'nenhum horário disponível')
})

test('botão só oferece enviar quando há o que enviar', () => {
  assert.equal(describePostButton(null), 'Calculando...')
  assert.equal(describePostButton({ total: 5 }, { loading: true }), 'Calculando...')
  assert.equal(describePostButton({ total: 0 }), 'Nada para enviar')
  assert.equal(describePostButton({ total: 1 }), 'Enviar 1 vídeo')
  assert.equal(describePostButton({ total: 5 }), 'Enviar 5 vídeos')
})

test('enviando tem precedência sobre qualquer outro estado', () => {
  assert.equal(describePostButton({ total: 5 }, { posting: true }), 'Enviando...')
  assert.equal(describePostButton(null, { posting: true }), 'Enviando...')
})
