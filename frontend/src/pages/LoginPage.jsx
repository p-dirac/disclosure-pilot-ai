import React, { useState } from "react"
import { authApi } from '../api/client'
import { useAuth } from '../hooks/useAuth'

export default function LoginPage() {
  const { login } = useAuth()
  const [mode, setMode] = useState('login') // 'login' | 'signup' | 'reset'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async () => {
    if (!email || !password) return

    setError('')
    setSuccess('')
    setLoading(true)

    try {
      if (mode === 'login') {
        await login(email, password)
        // navigation handled by App
      } else if (mode === 'signup') {
        if (password !== confirmPassword) {
          setError('Passwords do not match')
          setLoading(false)
          return
        }
        await authApi.register(email, password)
        setSuccess('Account created! Please log in.')
        setMode('login')
      } else if (mode === 'reset') {
        if (password !== confirmPassword) {
          setError('Passwords do not match')
          setLoading(false)
          return
        }
        await authApi.resetPassword(email, password)
        setSuccess('Password reset successfully. Please log in.')
        setMode('login')
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'An error occurred. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const titles = {
    login: 'Sign In',
    signup: 'Create Account',
    reset: 'Reset Password',
  }

  return (
    <div className="login-wrapper">
      <div className="login-card">
        <div className="login-title">Disclosure Pilot AI</div>
        <div className="login-subtitle">
          {mode === 'login' && 'Generate professional financial reports'}
          {mode === 'signup' && 'Create your account'}
          {mode === 'reset' && 'Reset your password'}
        </div>

        <div className="form-group">
          <label htmlFor="email">Email Address</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="you@company.com"
            onKeyDown={e => e.key === 'Enter' && handleSubmit()}
          />
        </div>

        <div className="form-group">
          <label htmlFor="password">{mode === 'reset' ? 'New Password' : 'Password'}</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSubmit()}
          />
        </div>

        {(mode === 'signup' || mode === 'reset') && (
          <div className="form-group">
            <label htmlFor="confirmPassword">Confirm Password</label>
            <input
              id="confirmPassword"
              type="password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSubmit()}
            />
          </div>
        )}

        {error && <div className="status-message status-error">{error}</div>}
        {success && <div className="status-message status-success">{success}</div>}

        <button
          className="btn btn-primary btn-block"
          onClick={handleSubmit}
          disabled={loading}
        >
          {loading && <span className="spinner" />}
          {titles[mode]}
        </button>

        <div className="login-toggle">
          {mode === 'login' && (
            <>
              <a onClick={() => { setMode('signup'); setError(''); setSuccess('') }}>Create account</a>
              {' · '}
              <a onClick={() => { setMode('reset'); setError(''); setSuccess('') }}>Forgot password?</a>
            </>
          )}
          {mode !== 'login' && (
            <a onClick={() => { setMode('login'); setError(''); setSuccess('') }}>
              Back to Sign In
            </a>
          )}
        </div>
      </div>
    </div>
  )
}
