import { Outlet, NavLink } from 'react-router-dom'
import { useEffect } from 'react'
import { useAuth } from '../context/AuthContext'
import { useBrand } from '../context/BrandContext'
import { getBrands } from '../api'
import './Layout.css'

export default function Layout() {
  const { user, logout } = useAuth()
  const { brandId, setBrandId, brands, refreshBrands } = useBrand()

  useEffect(() => {
    refreshBrands(getBrands)
  }, [])

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>Social Automation</h2>
          <div className="brand-selector">
            <label htmlFor="brand-select">Marca</label>
            <select
              id="brand-select"
              value={brandId}
              onChange={(e) => setBrandId(e.target.value || '')}
              className="brand-select"
            >
              <option value="">Selecione a marca</option>
              {brands.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
          </div>
        </div>
        <nav className="sidebar-nav">
          <NavLink to="/" end className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
            Dashboard
          </NavLink>
          <NavLink to="/gerar-cortes" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
            Gerar cortes
          </NavLink>
          <NavLink to="/editar-videos" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
            Editar Vídeos
          </NavLink>
          <NavLink to="/agendamento" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
            Agendamento
          </NavLink>
          <NavLink to="/intro-outro" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
            Intro / Outro
          </NavLink>
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
