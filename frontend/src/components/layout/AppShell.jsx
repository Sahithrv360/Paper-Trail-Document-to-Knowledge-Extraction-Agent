import React, { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import Header from './Header';
import Sidebar from './Sidebar';
import UploadModal from '../common/UploadModal';
import HelpModal from '../common/HelpModal';
import { aiService } from '../../services/aiService';

export default function AppShell() {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isHelpOpen, setIsHelpOpen] = useState(false);
  const [reviewCount, setReviewCount] = useState(0);
  const [conflictCount, setConflictCount] = useState(0);
  const [apiError, setApiError] = useState('');

  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        const [reviews, conflicts] = await Promise.all([aiService.getReviewItems(), aiService.getContradictions(), aiService.getWatcherStatus()]);
        if (mounted) {
          setReviewCount(reviews.filter(item => item.status === 'Pending').length);
          setConflictCount(conflicts.length);
        }
      } catch (_) { if (mounted) { setReviewCount(0); setConflictCount(0); } }
    };
    refresh();
    const timer = window.setInterval(refresh, 15000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    const onApiError = event => setApiError(event.detail || '');
    window.addEventListener('papertrail:api-error', onApiError);
    return () => window.removeEventListener('papertrail:api-error', onApiError);
  }, []);

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Header 
        onOpenUpload={() => setIsUploadOpen(true)}
        onOpenHelp={() => setIsHelpOpen(true)}
        reviewCount={reviewCount}
        contradictionCount={conflictCount}
      />
      
      <div style={{ display: 'flex', flex: 1, position: 'relative' }}>
        <Sidebar
          isCollapsed={isSidebarCollapsed}
          onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
          reviewCount={reviewCount}
          contradictionCount={conflictCount}
        />
        
        <main style={{
          flex: 1,
          padding: '28px 36px',
          maxWidth: '1600px',
          width: '100%',
          margin: '0 auto',
          overflowX: 'hidden'
        }}>
          {apiError && <div role="alert" style={{ marginBottom: 18, padding: '12px 16px', border: '1px solid #fecaca', borderRadius: 8, background: '#fef2f2', color: '#991b1b', fontSize: 14 }}>
            Backend request failed: {apiError}. Data is not being replaced with demo content.
          </div>}
          <Outlet context={{ openUpload: () => setIsUploadOpen(true) }} />
        </main>
      </div>

      <UploadModal 
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
      />

      <HelpModal 
        isOpen={isHelpOpen}
        onClose={() => setIsHelpOpen(false)}
      />
    </div>
  );
}
