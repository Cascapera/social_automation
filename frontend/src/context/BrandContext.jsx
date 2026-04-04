import { createContext, useContext, useState, useEffect } from 'react'

const STORAGE_KEY = 'selected_brand_id'
const FACTORY_STORAGE_KEY = 'selected_factory_id'
const VIEW_MODE_STORAGE_KEY = 'selected_view_mode'

const BrandContext = createContext(null)

export function BrandProvider({ children }) {
  const [brandId, setBrandIdState] = useState('')
  const [factoryId, setFactoryIdState] = useState('')
  const [viewMode, setViewModeState] = useState('brand')
  const [brands, setBrands] = useState([])
  const [factories, setFactories] = useState([])

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) setBrandIdState(saved)
    const savedFactory = localStorage.getItem(FACTORY_STORAGE_KEY)
    if (savedFactory) setFactoryIdState(savedFactory)
    const savedMode = localStorage.getItem(VIEW_MODE_STORAGE_KEY)
    if (savedMode === 'brand' || savedMode === 'factory') setViewModeState(savedMode)
  }, [])

  const setBrandId = (id) => {
    const value = id ? String(id) : ''
    setBrandIdState(value)
    if (value) {
      localStorage.setItem(STORAGE_KEY, value)
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }

  const setFactoryId = (id) => {
    const value = id ? String(id) : ''
    setFactoryIdState(value)
    if (value) {
      localStorage.setItem(FACTORY_STORAGE_KEY, value)
    } else {
      localStorage.removeItem(FACTORY_STORAGE_KEY)
    }
  }

  const setViewMode = (mode) => {
    const value = mode === 'factory' ? 'factory' : 'brand'
    setViewModeState(value)
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, value)
  }

  const refreshBrands = async (fetchBrands) => {
    try {
      const list = await fetchBrands()
      setBrands(list || [])
      const current = brandId
      if (current && !(list || []).find((b) => String(b.id) === current)) {
        setBrandId((list || [])[0]?.id ?? '')
      }
    } catch {
      setBrands([])
    }
  }

  return (
    <BrandContext.Provider
      value={{
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
      }}
    >
      {children}
    </BrandContext.Provider>
  )
}

export function useBrand() {
  const ctx = useContext(BrandContext)
  if (!ctx) throw new Error('useBrand deve ser usado dentro de BrandProvider')
  return ctx
}
