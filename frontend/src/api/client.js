/**
 * Axios API client - handles JWT auth headers and base URL.
 */
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Attach JWT token to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Redirect to login on 401
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('access_token')
      window.location.href = '/'
    }
    return Promise.reject(err)
  }
)

export default api

// ─── Auth ──────────────────────────────────────────────────────────────────

export const authApi = {
  login: (email, password) => {
    const form = new URLSearchParams()
    form.append('username', email)
    form.append('password', password)
    return axios.post('/api/auth/login', form, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
  },
  register: (email, password) =>
    api.post('/auth/register', { email, password }),
  resetPassword: (email, new_password) =>
    api.post('/auth/reset-password', { email, new_password }),
  getMe: () => api.get('/auth/me'),
}

// ─── 10-Q ──────────────────────────────────────────────────────────────────

export const tenQApi = {
  getFinancials: (year, quarter) =>
    api.post('/10q/financials', { year, quarter }),
  getMDA: (year, quarter, mdaContext) =>
    api.post('/10q/mda', { year, quarter, mda_context: mdaContext }),
  saveFinancials: (year, quarter) =>
    api.post('/10q/save-financials', { year, quarter }),
  saveMDA: (year, quarter, narrative) =>
    api.post('/10q/save-mda', { year, quarter, narrative }),
  getNotesList: (quarter) =>
    api.get('/10q/notes-list', { params: { quarter } }),
  getNotes: (year, quarter, notesContext) =>
    api.post('/10q/notes', { year, quarter, notes_context: notesContext }),
  saveNotes: (year, quarter, narrative) =>
    api.post('/10q/save-notes', { year, quarter, narrative }),
  generateReport: (year, quarter) =>
    api.post('/10q/generate-report', { year, quarter }),
  getReportsList: () =>
    api.get('/10q/reports-list'),
  generateEdgarHtml: (filename) =>
    api.post('/10q/generate-edgar-html', { filename }),
}

// ─── 10-K ──────────────────────────────────────────────────────────────────

export const tenKApi = {
  getFinancials: (year) =>
    api.post('/10k/financials', { year }),
  getMDA: (year, mdaContext) =>
    api.post('/10k/mda', { year, mda_context: mdaContext }),
  saveFinancials: (year) =>
    api.post('/10k/save-financials', { year }),
  saveMDA: (year, narrative) =>
    api.post('/10k/save-mda', { year, narrative }),
  getNotesList: () =>
    api.get('/10k/notes-list'),
  getNotes: (year) =>
    api.post('/10k/notes', { year }),
  saveNotes: (year, narrative) =>
    api.post('/10k/save-notes', { year, narrative }),
  generateReport: (year) =>
    api.post('/10k/generate-report', { year }),
  getReportsList: () =>
    api.get('/10k/reports-list'),
  generateEdgarHtml: (filename) =>
    api.post('/10k/generate-edgar-html', { filename }),
}
