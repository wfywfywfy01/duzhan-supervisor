/**
 * API 客户端：统一 fetch 封装（credentials include、错误归一化）。
 * 与 FastAPI 错误契约 {detail, code} 对齐。
 */
export interface ApiError {
  status: number
  detail: string
  code?: string
}

export class HttpError extends Error {
  status: number
  code?: string
  detail: string

  constructor(status: number, detail: string, code?: string) {
    super(detail)
    this.status = status
    this.code = code
    this.detail = detail
  }
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  const res = await apiRequest(path)
  return (await res.json()) as T
}

export async function apiPost<T = unknown>(path: string, body?: unknown, headers?: HeadersInit): Promise<T> {
  const requestHeaders = new Headers(headers)
  if (body !== undefined) requestHeaders.set('Content-Type', 'application/json')
  const res = await apiRequest(path, {
    method: 'POST',
    credentials: 'include',
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return (await res.json()) as T
}

export async function apiPut<T = unknown>(path: string, body?: unknown): Promise<T> {
  const res = await apiRequest(path, {
    method: 'PUT',
    credentials: 'include',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return (await res.json()) as T
}

export async function apiPatch<T = unknown>(path: string, body?: unknown): Promise<T> {
  const res = await apiRequest(path, {
    method: 'PATCH',
    credentials: 'include',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return (await res.json()) as T
}

export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(path, { cache: 'no-store', credentials: 'include', ...init })
  if (!res.ok) {
    const error = await toHttpError(res)
    // Credential verification failures must stay in their own form.
    const credentialPath = ['/api/auth/login', '/api/auth/change-password', '/api/knowledge/reauth'].includes(path.split('?')[0])
    if (res.status === 401 && !credentialPath && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('duzhan-session-expired'))
    }
    throw error
  }
  return res
}

/** 后端可能用 {message, errors} 结构化返回校验错误（如督战官配置启用被挡）。 */
function objectDetail(detail: unknown): string {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return ''
  const shape = detail as { message?: unknown; errors?: unknown }
  const parts: string[] = []
  if (typeof shape.message === 'string' && shape.message) parts.push(shape.message)
  if (Array.isArray(shape.errors)) parts.push(...shape.errors.map((item) => String(item)))
  return parts.join('；')
}

async function toHttpError(res: Response): Promise<HttpError> {
  try {
    const payload = (await res.json()) as { detail?: unknown; code?: string }
    const detail = typeof payload.detail === 'string' ? payload.detail : Array.isArray(payload.detail)
      ? payload.detail.map((issue: { loc?: unknown[]; msg?: string }) =>
          [issue.loc?.filter((part) => part !== 'body' && part !== 'query').join('.'), issue.msg].filter(Boolean).join(': '),
        ).filter(Boolean).join('；')
      : objectDetail(payload.detail)
    return new HttpError(res.status, detail || `HTTP ${res.status}`, payload.code)
  } catch {
    return new HttpError(res.status, `HTTP ${res.status}`)
  }
}
