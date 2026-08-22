import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { AuthProvider, useAuth } from '../hooks/useAuth'
import { useState } from "react";
import Navbar from './Navbar'
import HomePage from '../pages/HomePage'
import Prep10QPage from '../pages/Prep10QPage'
import Report10QPage from '../pages/Report10QPage'
import { Edgar10QPage } from '../pages/Edgar10QPage'
import Prep10KPage from '../pages/Prep10KPage'
import { Report10KPage, Edgar10KPage } from '../pages/Report10KPage'
import NotFoundPage from '../pages/NotFoundPage';
import {UserGuideDialog, AboutDialog} from './AppDialogs'
import '../styles/global.css'

export default function ProtectedLayout() {
  const { user, loading } = useAuth()
  if (loading) return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading...</div>
  if (!user) return <Navigate to="/" replace />

  const navigate = useNavigate();
  const [dialog, setDialog] = useState(null); // 'user-guide' | 'about' | null
  const routes = {
    "home":       "/home",
    "10q-prep":   "/prep-10q",
    "10q-report": "/report-10q",
    "10q-edgar":  "/edgar-10q",
    "10k-prep":   "/prep-10k",
    "10k-report": "/report-10k",
    "10k-edgar":  "/edgar-10k",
  };

  const handleNavigate = (key) => {
	console.log("handleNavigate called with:", key);  
	if (key === "user-guide" || key === "about") {
       setDialog(key);
       return;
    }
	console.log("resolved route:", routes[key]);
    if (routes[key]){ 
	   console.log("navigating to:", routes[key]);
	   navigate(routes[key]);
	}
  };
  
  return (
    <>
      <Navbar onNavigate={handleNavigate} />
	  {dialog === "user-guide" && <UserGuideDialog onClose={() => setDialog(null)} />}
      {dialog === "about"      && <AboutDialog      onClose={() => setDialog(null)} />}
      <Routes>
        <Route path="/home" element={<HomePage />} />
        <Route path="/prep-10q" element={<Prep10QPage />} />
        <Route path="/report-10q" element={<Report10QPage />} />
        <Route path="/edgar-10q" element={<Edgar10QPage />} />
        <Route path="/prep-10k" element={<Prep10KPage />} />
        <Route path="/report-10k" element={<Report10KPage />} />
        <Route path="/edgar-10k" element={<Edgar10KPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </>
  )
}