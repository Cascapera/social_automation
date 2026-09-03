import { useEffect, useMemo, useState } from 'react'
import { useBrand } from '../context/BrandContext'
import {
  getBrands,
  getFactories,
  getBrandCategories,
  createBrandCategory,
  updateBrandCategory,
  deleteBrandCategory,
  reactivateBrandCategory,
} from '../api'
import './FactoryConfig.css'

export default function FactoryConfig() {
  const { factoryId, setFactoryId, factories, setFactories } = useBrand()

  const [categories, setCategories] = useState([])
  const [brands, setBrands] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const [newLabel, setNewLabel] = useState('')
  const [creating, setCreating] = useState(false)

  // Edição inline do label
  const [editingId, setEditingId] = useState(null)
  const [editingLabel, setEditingLabel] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)

  useEffect(() => {
    getFactories()
      .then(setFactories)
      .catch(() => setFactories([]))
  }, [])

  useEffect(() => {
    if (!factoryId) {
      setCategories([])
      setBrands([])
      return
    }
    setLoading(true)
    setError('')
    Promise.all([
      getBrandCategories(factoryId, { includeInactive: true }),
      getBrands(factoryId),
    ])
      .then(([cats, bs]) => {
        setCategories(cats || [])
        setBrands(Array.isArray(bs) ? bs : (bs?.results || bs?.items || []))
      })
      .catch((e) => setError(e.message || 'Erro ao carregar'))
      .finally(() => setLoading(false))
  }, [factoryId])

  const codeToLabel = useMemo(() => {
    const m = {}
    for (const c of categories) m[c.code] = c.label
    return m
  }, [categories])

  async function reload() {
    if (!factoryId) return
    const [cats, bs] = await Promise.all([
      getBrandCategories(factoryId, { includeInactive: true }),
      getBrands(factoryId),
    ])
    setCategories(cats || [])
    setBrands(Array.isArray(bs) ? bs : (bs?.results || bs?.items || []))
  }

  async function handleCreate(e) {
    e.preventDefault()
    const label = (newLabel || '').trim()
    if (!label) {
      setError('Informe o nome da categoria.')
      return
    }
    setCreating(true)
    setError('')
    setInfo('')
    try {
      await createBrandCategory(factoryId, label)
      setNewLabel('')
      await reload()
      setInfo('Categoria criada.')
    } catch (e) {
      setError(e.message || 'Erro ao criar categoria.')
    } finally {
      setCreating(false)
    }
  }

  function startEdit(cat) {
    setEditingId(cat.id)
    setEditingLabel(cat.label)
    setError('')
    setInfo('')
  }

  async function saveEdit(cat) {
    const label = (editingLabel || '').trim()
    if (!label || label === cat.label) {
      setEditingId(null)
      return
    }
    setSavingEdit(true)
    setError('')
    try {
      await updateBrandCategory(cat.id, { label })
      await reload()
      setEditingId(null)
      setInfo('Categoria renomeada.')
    } catch (e) {
      setError(e.message || 'Erro ao renomear.')
    } finally {
      setSavingEdit(false)
    }
  }

  async function handleDelete(cat) {
    if (!confirm(`Excluir categoria "${cat.label}"? Brands que ainda usam ela bloqueiam a exclusão.`)) return
    setError('')
    setInfo('')
    try {
      await deleteBrandCategory(cat.id)
      await reload()
      setInfo('Categoria desativada.')
    } catch (e) {
      setError(e.message || 'Erro ao excluir.')
    }
  }

  async function handleReactivate(cat) {
    setError('')
    setInfo('')
    try {
      await reactivateBrandCategory(cat.id)
      await reload()
      setInfo('Categoria reativada.')
    } catch (e) {
      setError(e.message || 'Erro ao reativar.')
    }
  }

  const activeCategories = categories.filter((c) => c.is_active)
  const inactiveCategories = categories.filter((c) => !c.is_active)

  return (
    <div className="factory-config">
      <h1>Factory Config</h1>
      <p className="form-hint">
        Gerencie as categorias temáticas da factory e veja quais brands estão vinculadas a cada uma.
        O código interno de cada categoria é gerado automaticamente e permanece estável — só o nome exibido pode ser editado.
      </p>

      <div className="form-group">
        <label>Factory</label>
        <select
          value={factoryId || ''}
          onChange={(e) => setFactoryId(e.target.value || '')}
        >
          <option value="">Selecione uma factory</option>
          {(factories || []).map((f) => (
            <option key={f.id} value={f.id}>{f.name}</option>
          ))}
        </select>
      </div>

      {error && <div className="form-error">{error}</div>}
      {info && <div className="form-info">{info}</div>}

      {!factoryId ? (
        <p className="form-hint">Selecione uma factory para ver as categorias e brands.</p>
      ) : loading ? (
        <p className="form-hint">Carregando...</p>
      ) : (
        <>
          <section className="panel">
            <h2>Categorias</h2>

            <form onSubmit={handleCreate} className="category-create">
              <input
                type="text"
                placeholder="Nome da nova categoria (ex: Esportes)"
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                maxLength={120}
              />
              <button type="submit" disabled={creating}>
                {creating ? 'Criando...' : '+ Criar categoria'}
              </button>
            </form>

            <table className="category-table">
              <thead>
                <tr>
                  <th>Nome exibido</th>
                  <th>Código</th>
                  <th>Brands usando</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {activeCategories.map((cat) => {
                  const inUseIds = cat.in_use_by_brand_ids || []
                  const inUseNames = inUseIds
                    .map((id) => brands.find((b) => b.id === id)?.name)
                    .filter(Boolean)
                  return (
                    <tr key={cat.id}>
                      <td>
                        {editingId === cat.id ? (
                          <div className="inline-edit">
                            <input
                              type="text"
                              value={editingLabel}
                              onChange={(e) => setEditingLabel(e.target.value)}
                              maxLength={120}
                              autoFocus
                            />
                            <button type="button" onClick={() => saveEdit(cat)} disabled={savingEdit}>
                              Salvar
                            </button>
                            <button type="button" onClick={() => setEditingId(null)}>
                              Cancelar
                            </button>
                          </div>
                        ) : (
                          <>
                            <strong>{cat.label}</strong>{' '}
                            <button type="button" className="btn-link" onClick={() => startEdit(cat)}>
                              Renomear
                            </button>
                          </>
                        )}
                      </td>
                      <td><code>{cat.code}</code></td>
                      <td>
                        {inUseNames.length === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          inUseNames.join(', ')
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn-danger"
                          onClick={() => handleDelete(cat)}
                          disabled={inUseIds.length > 0}
                          title={inUseIds.length > 0 ? 'Troque a categoria das brands listadas antes de excluir.' : ''}
                        >
                          Excluir
                        </button>
                      </td>
                    </tr>
                  )
                })}
                {activeCategories.length === 0 && (
                  <tr><td colSpan="4" className="muted">Nenhuma categoria ativa.</td></tr>
                )}
              </tbody>
            </table>

            {inactiveCategories.length > 0 && (
              <>
                <h3>Categorias inativas</h3>
                <table className="category-table">
                  <thead>
                    <tr>
                      <th>Nome exibido</th>
                      <th>Código</th>
                      <th>Ações</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inactiveCategories.map((cat) => (
                      <tr key={cat.id} className="inactive">
                        <td>{cat.label}</td>
                        <td><code>{cat.code}</code></td>
                        <td>
                          <button type="button" onClick={() => handleReactivate(cat)}>
                            Reativar
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </section>

          <section className="panel">
            <h2>Brands desta factory</h2>
            <p className="form-hint">
              Lista somente leitura. Para alterar nome ou categoria de uma brand, abra &quot;Mídias da marca&quot; / Brand.
            </p>
            <table className="category-table">
              <thead>
                <tr>
                  <th>Brand</th>
                  <th>Categoria vinculada</th>
                </tr>
              </thead>
              <tbody>
                {brands.map((b) => {
                  const label = b.theme_category ? (codeToLabel[b.theme_category] || b.theme_category) : ''
                  return (
                    <tr key={b.id}>
                      <td>{b.name}</td>
                      <td>{label || <span className="muted">(sem categoria)</span>}</td>
                    </tr>
                  )
                })}
                {brands.length === 0 && (
                  <tr><td colSpan="2" className="muted">Nenhuma brand cadastrada nesta factory.</td></tr>
                )}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  )
}
