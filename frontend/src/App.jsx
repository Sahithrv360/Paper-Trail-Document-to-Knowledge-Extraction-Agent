import React, { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';

const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const DocumentsPage = lazy(() => import('./pages/DocumentsPage'));
const DocumentProcessingPage = lazy(() => import('./pages/DocumentProcessingPage'));
const DocumentInspectorPage = lazy(() => import('./pages/DocumentInspectorPage'));
const KnowledgeRepositoryPage = lazy(() => import('./pages/KnowledgeRepositoryPage'));
const KnowledgeGraphPage = lazy(() => import('./pages/KnowledgeGraphPage'));
const AskKnowledgePage = lazy(() => import('./pages/AskKnowledgePage'));
const ReviewCenterPage = lazy(() => import('./pages/ReviewCenterPage'));
const ContradictionsPage = lazy(() => import('./pages/ContradictionsPage'));
const SemanticModelPage = lazy(() => import('./pages/SemanticModelPage'));
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage'));
const ArchitecturePage = lazy(() => import('./pages/ArchitecturePage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', color: '#475569', fontFamily: 'system-ui, sans-serif' }}>Loading PaperTrail…</main>}>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="processing/:docId" element={<DocumentProcessingPage />} />
          <Route path="documents/inspector/:docId" element={<DocumentInspectorPage />} />
          <Route path="knowledge" element={<KnowledgeRepositoryPage />} />
          <Route path="knowledge/entities" element={<KnowledgeRepositoryPage />} />
          <Route path="knowledge/relationships" element={<KnowledgeRepositoryPage />} />
          <Route path="knowledge/facts" element={<KnowledgeRepositoryPage />} />
          <Route path="knowledge/evidence" element={<KnowledgeRepositoryPage />} />
          <Route path="knowledge/conversations" element={<Navigate to="/knowledge" replace />} />
          <Route path="entities" element={<Navigate to="/knowledge/entities" replace />} />
          <Route path="relationships" element={<Navigate to="/knowledge/relationships" replace />} />
          <Route path="facts" element={<Navigate to="/knowledge/facts" replace />} />
          <Route path="evidence" element={<Navigate to="/knowledge/evidence" replace />} />
          <Route path="conversations" element={<Navigate to="/knowledge" replace />} />
          <Route path="graph" element={<KnowledgeGraphPage />} />
          <Route path="ask" element={<AskKnowledgePage />} />
          <Route path="review" element={<ReviewCenterPage />} />
          <Route path="contradictions" element={<ContradictionsPage />} />
          <Route path="semantic-model" element={<SemanticModelPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="architecture" element={<ArchitecturePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
