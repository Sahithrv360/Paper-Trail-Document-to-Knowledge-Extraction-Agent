import React, { useEffect, useState } from 'react';
import { FileText, Cpu, ShieldCheck, Database, Network, Search, RefreshCw, AlertCircle } from 'lucide-react';
import { aiService } from '../services/aiService';

const stages = [
  { icon: FileText, title: 'Folder selection and authenticated upload', detail: 'The UI lets you select a local folder. It uploads supported PDF, JPG, JPEG, and PNG files individually to the authenticated /api/upload endpoint. The backend enforces the configured per-file limit, sanitizes filenames, resolves duplicate names, and copies each file into the configured watched folder.' },
  { icon: RefreshCw, title: 'Watched folder and document registry', detail: 'The separate watcher scans the destination folder, registers new files, and tracks queue state. Uploaded files appear in the UI after the watcher next scans them. The review API reports registry counts but does not monitor whether the watcher process is alive.' },
  { icon: Cpu, title: 'Configured extraction provider', detail: 'The backend uses its configured provider and parser. OCR, Donut, and optional remote vision processing depend on the local environment settings and installed dependencies.' },
  { icon: ShieldCheck, title: 'Validation and review', detail: 'Extracted records retain validation status, confidence basis, source evidence, and review state. Review decisions apply to a specific extraction version.' },
  { icon: Database, title: 'SQLite graph ledger', detail: 'SQLite stores document versions, claims, graph nodes, relationships, and review history. The graph explorer reads this backend graph export.' },
  { icon: Network, title: 'Optional Neo4j projection', detail: 'Neo4j is an optional projection that can be synchronized from the SQLite graph. It is not the source of truth for the UI graph export.' },
  { icon: Search, title: 'Graph-backed question answering', detail: 'The Ask page submits questions to the backend retrieval/agent endpoint. Retrieved graph evidence and answer-generation status are returned by the service.' },
];

export default function ArchitecturePage() {
  const [runtime, setRuntime] = useState(null);
  const [error, setError] = useState('');
  const load = async () => { setError(''); try { setRuntime(await aiService.getWatcherStatus()); } catch (err) { setError(err.message); } };
  useEffect(() => { load(); }, []);

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 1050 }}>
    <header style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}><div><span className="badge badge-blue">Current implementation</span><h1 style={{ fontSize: '1.75rem', margin: '8px 0 4px' }}>System Architecture</h1><p style={{ color: 'var(--text-secondary)', margin: 0 }}>This page describes the active MVP flow and current backend configuration.</p></div><button className="btn btn-outline btn-sm" onClick={load}><RefreshCw size={14} /> Refresh runtime</button></header>
    {error && <div className="card" role="alert" style={{ padding: 14, color: '#991b1b' }}><AlertCircle size={16} /> Backend status unavailable: {error}</div>}
    {runtime && <div className="card" style={{ padding: 16, display: 'flex', flexWrap: 'wrap', gap: 18 }}><span>Provider: <strong>{runtime.extraction_provider}</strong></span><span>Parser: <strong>{runtime.ocr_engine}</strong></span><span>Extraction device: <strong>{runtime.device}</strong></span><span>Answer backend: <strong>{runtime.answer_backend}</strong></span><span>Answer model: <strong>{runtime.answer_model}</strong></span><span>Registered files: <strong>{runtime.registered_documents}</strong></span><span>Neo4j configured: <strong>{runtime.neo4j_configured ? 'Yes' : 'No'}</strong></span></div>}
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>{stages.map(({ icon: Icon, title, detail }, index) => <React.Fragment key={title}><article className="card" style={{ display: 'flex', gap: 14, padding: 20 }}><div style={{ color: '#2563eb', paddingTop: 3 }}><Icon size={20} /></div><div><div style={{ color: 'var(--text-muted)', fontSize: 12 }}>STEP {index + 1}</div><h2 style={{ margin: '3px 0 6px', fontSize: 17 }}>{title}</h2><p style={{ margin: 0, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{detail}</p></div></article></React.Fragment>)}</div>
    <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>Flow: choose a folder in the React UI → authenticated upload API → configured watched folder → separate watcher and registry → extraction and review → SQLite graph → graph search and agent answers. The UI server on port 8765 serves the built React app and authenticated API. For live frontend development on port 5173, Vite proxies <code>/api</code> to <code>http://127.0.0.1:8765</code>.</p>
  </div>;
}
