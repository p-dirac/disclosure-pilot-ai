import React from 'react'
import { useAuth } from '../hooks/useAuth'

export default function HomePage() {
  const { user } = useAuth()
  return (
    <div className="page-container">
      <div style={{ textAlign: 'center', paddingTop: '3rem' }}>
        <h1 style={{ color: 'var(--color-accent)', marginBottom: '1rem' }}>
          Welcome to Disclosure Pilot AI
        </h1>
        <p style={{ fontSize: '1.2rem', maxWidth: '600px', margin: '0 auto 2rem', lineHeight: 1.7 }}>
          Generate professional quarterly and yearly financial reports.
          {user && ` Signed in as ${user.email}.`}
        </p>
        
      </div>
    </div>
  )
}
