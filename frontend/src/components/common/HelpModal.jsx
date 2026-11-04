import React from 'react';
import { X, HelpCircle, FileText, Network, MessageSquareText, ShieldCheck } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

export default function HelpModal({ isOpen, onClose }) {
  const navigate = useNavigate();
  if (!isOpen) return null;
  const links = [
    { icon: FileText, title: 'Documents', detail: 'See files registered by the backend watcher.', path: '/documents' },
    { icon: Network, title: 'Knowledge graph', detail: 'Explore nodes and relationships exported from SQLite.', path: '/graph' },
    { icon: MessageSquareText, title: 'Ask Knowledge', detail: 'Submit a question to the backend graph QA agent.', path: '/ask' },
    { icon: ShieldCheck, title: 'Review Center', detail: 'Approve an active extraction version after checking its source.', path: '/review' },
  ];
  const open = path => { onClose(); navigate(path); };

  return <div className="modal-overlay" onClick={onClose}>
    <div className="modal-content" onClick={event => event.stopPropagation()} style={{ maxWidth: 650, padding: 26 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}><div style={{ display: 'flex', gap: 10, alignItems: 'center' }}><HelpCircle size={22} color="#2563eb" /><div><h2 style={{ margin: 0, fontSize: 20 }}>Paper Trail guide</h2><p style={{ margin: '4px 0 0', color: 'var(--text-secondary)', fontSize: 13 }}>Navigate the pages connected to the running backend.</p></div></div><button className="btn btn-outline btn-sm" onClick={onClose} aria-label="Close"><X size={16} /></button></header>
      <div style={{ display: 'grid', gap: 10 }}>{links.map(({ icon: Icon, title, detail, path }) => <button key={path} className="card" onClick={() => open(path)} style={{ padding: 14, display: 'flex', gap: 12, alignItems: 'center', textAlign: 'left', cursor: 'pointer' }}><Icon size={19} color="#2563eb" /><span><strong>{title}</strong><span style={{ display: 'block', color: 'var(--text-secondary)', fontSize: 13 }}>{detail}</span></span></button>)}</div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 0 }}>Choose Upload folder to copy supported local files into the configured backend watch folder. The separate watcher registers them on its next scan.</p>
    </div>
  </div>;
}
