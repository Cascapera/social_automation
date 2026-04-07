import './PaginationControls.css'

const DEFAULT_PAGE_SIZE = 25

/**
 * Controles de paginação para listas da API (count + page).
 */
export default function PaginationControls({
  page = 1,
  pageSize = DEFAULT_PAGE_SIZE,
  totalCount = 0,
  onPageChange,
  disabled = false,
  className = '',
}) {
  const totalPages = totalCount > 0 && pageSize > 0 ? Math.max(1, Math.ceil(totalCount / pageSize)) : 1
  const canPrev = page > 1 && !disabled
  const canNext = page < totalPages && !disabled

  return (
    <div className={`pagination-controls ${className}`.trim()}>
      <button
        type="button"
        className="pagination-btn"
        disabled={!canPrev}
        onClick={() => onPageChange(page - 1)}
      >
        Anterior
      </button>
      <span className="pagination-info">
        Página {page} de {totalPages}
        {totalCount > 0 ? ` · ${totalCount.toLocaleString('pt-BR')} itens` : ''}
      </span>
      <button
        type="button"
        className="pagination-btn"
        disabled={!canNext}
        onClick={() => onPageChange(page + 1)}
      >
        Próxima
      </button>
    </div>
  )
}

export { DEFAULT_PAGE_SIZE }
