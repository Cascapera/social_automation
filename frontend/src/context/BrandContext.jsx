import { createContext, useContext, useState, useEffect } from 'react'

const STORAGE_KEY = 'selected_brand_id'

const BrandContext = createContext(null)

export function BrandProvider({ children }) {
  const [brandId, setBrandIdState] = useState('')
  const [brands, setBrands] = useState([])

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) setBrandIdState(saved)
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
    <BrandContext.Provider value={{ brandId, setBrandId, brands, setBrands, refreshBrands }}>
      {children}
    </BrandContext.Provider>
  )
}

export function useBrand() {
  const ctx = useContext(BrandContext)
  if (!ctx) throw new Error('useBrand deve ser usado dentro de BrandProvider')
  return ctx
}
