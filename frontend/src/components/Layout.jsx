import { Outlet, NavLink } from 'react-router-dom'
import { useEffect } from 'react'
import { useAuth } from '../context/AuthContext'
import { useBrand } from '../context/BrandContext'
import { getBrands, getFactories } from '../api'
import './Layout.css'

export default function Layout() {
  const { user, logout } = useAuth()
  const {
    brandId,
    setBrandId,
    factoryId,
    setFactoryId,
    viewMode,
    setViewMode,
    brands,
    setBrands,
    factories,
    setFactories,
    refreshBrands,
  } = useBrand()

  useEffect(() => {
    refreshBrands(getBrands)
    getFactories().then(setFactories).catch(() => setFactories([]))
  }, [])

  const brandsForFactory = viewMode === 'factory' && factoryId
    ? brands.filter((b) => String(b.factory || '') === String(factoryId))
    : brands

  const brandMenuLinks = [
    { to: '/', label: 'Dashboard', end: true },
    { to: '/gerar-cortes', label: 'Gerar cortes' },
    { to: '/editar-videos', label: 'Editar Vídeos' },
    { to: '/agendamento', label: 'Agendamento' },
    { to: '/banco-videos', label: 'Banco de Vídeos' },
    { to: '/midias-marca', label: 'Mídias da marca' },
    { to: '/cortes-automaticos', label: 'Cortes Automáticos' },
    { to: '/multiple-creator', label: 'Multiple-Creator' },
    { to: '/contas', label: 'Contas' },
  ]

  const factoryMenuLinks = [
    { to: '/', label: 'Dashboard', end: true },
    { to: '/factory-config', label: 'Factory Config' },
    { to: '/agendamento', label: 'Agendamento (Factory)' },
    { to: '/canais-busca', label: 'Canais de Busca' },
    { to: '/cortes-automaticos', label: 'Criação de Cortes' },
    { to: '/multiple-creator', label: 'Multiple-Creator' },
    { to: '/banco-videos', label: 'Banco de Vídeos' },
    { to: '/midias-marca', label: 'Brands da Factory' },
    { to: '/contas', label: 'Contas dos Canais' },
  ]

  const menuLinks = viewMode === 'factory' ? factoryMenuLinks : brandMenuLinks

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>Social Automation</h2>
          <div className="brand-selector">
            <label htmlFor="view-mode-select">Contexto</label>
            <select
              id="view-mode-select"
              value={viewMode}
              onChange={(e) => setViewMode(e.target.value)}
              className="brand-select"
            >
              <option value="brand">Marca (individual)</option>
              <option value="factory">Factory (multicanal)</option>
            </select>
          </div>

          {viewMode === 'factory' && (
            <div className="brand-selector">
              <label htmlFor="factory-select">Factory</label>
              <select
                id="factory-select"
                value={factoryId}
                onChange={(e) => {
                  const nextFactory = e.target.value || ''
                  setFactoryId(nextFactory)
                  const firstBrand = brands.find((b) => String(b.factory || '') === String(nextFactory))
                  setBrandId(firstBrand?.id || '')
                }}
                className="brand-select"
              >
                <option value="">Selecione a factory</option>
                {factories.map((f) => (
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))}
              </select>
            </div>
          )}

          <div className="brand-selector">
            <label htmlFor="brand-select">
              {viewMode === 'factory' ? 'Brand da factory (opcional)' : 'Marca'}
            </label>
            <select
              id="brand-select"
              value={brandId}
              onChange={(e) => setBrandId(e.target.value || '')}
              className="brand-select"
            >
              <option value="">
                {viewMode === 'factory' ? 'Todas as brands da factory' : 'Selecione a marca'}
              </option>
              {brandsForFactory.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
          </div>
        </div>
        <nav className="sidebar-nav">
          {menuLinks.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={!!item.end}
              className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button onClick={logout} className="btn-logout">Sair</button>
        </div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}
