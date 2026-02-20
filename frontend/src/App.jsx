import { Routes, Route, Navigate } from 'react-router-dom'
import './App.css'
import { useAuth } from './context/AuthContext'
import Login from './pages/Login'
import Register from './pages/Register'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import GerarCortes from './pages/GerarCortes'
import EditarVideos from './pages/EditarVideos'
import Agendamento from './pages/Agendamento'
import IntroOutro from './pages/IntroOutro'

function PrivateRoute({ children }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="loading">Carregando...</div>
  if (!user) return <Navigate to="/login" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/"
        element={
          <PrivateRoute>
            <Layout />
          </PrivateRoute>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="gerar-cortes" element={<GerarCortes />} />
        <Route path="editar-videos" element={<EditarVideos />} />
        <Route path="agendamento" element={<Agendamento />} />
        <Route path="intro-outro" element={<IntroOutro />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
