import React, { useEffect, useState } from 'react';
import { Cpu, FolderOpen, Database, RefreshCw, AlertCircle } from 'lucide-react';
import { aiService } from '../services/aiService';

export default function SettingsPage() {
  const [runtime, setRuntime] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setError('');
    try { setRuntime(await aiService.getSettings()); }
    catch (err) { setError(err.message || 'Could not read backend configuration.'); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);

  const rows = runtime ? [
    ['Extraction provider', runtime.extraction_provider],
    ['OCR engine', runtime.ocr_engine],
    ['Execution device', runtime.device],
    ['Answer backend', runtime.answer_backend],
    ['Answer model', runtime.answer_model],
    ['Document completion threshold', `${Math.round(runtime.completion_threshold * 100)}%`],
    ['Watch folder exists', runtime.watch_folder_exists ? 'Yes' : 'No'],
    ['Watcher process', runtime.watcher_process],
    ['Neo4j configured', runtime.neo4j_configured ? 'Yes' : 'No'],
    ['Registered documents', runtime.registered_documents],
    ['Queued documents', runtime.queued_documents],
    ['Failed documents', runtime.failed_documents],
  ] : [];

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 900 }}>
    <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <div>
        <span className="badge badge-blue">Live backend configuration</span>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 800, margin: '8px 0 4px' }}>System Settings</h1>
        <p style={{ color: 'var(--text-secondary)', margin: 0 }}>Values are read from the running backend and its environment.</p>
      </div>
      <button className="btn btn-outline btn-sm" onClick={load} disabled={loading}><RefreshCw size={15} /> Refresh</button>
    </header>
    {error && <div className="card" role="alert" style={{ padding: 16, color: '#b91c1c' }}><AlertCircle size={16} /> {error}</div>}
    {loading && !runtime ? <div className="card" style={{ padding: 24 }}>Loading backend configuration…</div> : null}
    {runtime && <>
      <section className="card" style={{ padding: 22 }}>
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 0 }}><Cpu size={19} /> Runtime</h2>
        <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 16, margin: 0 }}>
          {rows.slice(0, 6).map(([label, value]) => <div key={label}><dt style={{ color: 'var(--text-muted)', fontSize: 13 }}>{label}</dt><dd style={{ margin: '4px 0 0', fontWeight: 700 }}>{String(value ?? 'Not configured')}</dd></div>)}
        </dl>
      </section>
      <section className="card" style={{ padding: 22 }}>
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 0 }}><FolderOpen size={19} /> Document intake</h2>
        <div style={{ overflowWrap: 'anywhere', fontFamily: 'var(--font-mono)', background: '#f8fafc', padding: 12, borderRadius: 8 }}>{runtime.watch_folder}</div>
        <p style={{ color: 'var(--text-secondary)', marginBottom: 0 }}>Supported: {(runtime.supported_extensions || []).join(', ') || 'None reported'}. The folder watcher runs as a separate process; its live process state is not currently monitored by this API.</p>
      </section>
      <section className="card" style={{ padding: 22 }}>
        <h2 style={{ marginTop: 0 }}>Document registry</h2>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20 }}>{rows.slice(9).map(([label, value]) => <div key={label}><span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{label}: </span><strong>{String(value ?? 0)}</strong></div>)}</div>
      </section>
      <section className="card" style={{ padding: 22 }}>
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 0 }}><Database size={19} /> Data connections</h2>
        <p style={{ margin: 0, color: 'var(--text-secondary)' }}>SQLite is the source of truth. Neo4j driver configured for this backend process: <strong>{runtime.neo4j_configured ? 'yes' : 'no'}</strong>.</p>
      </section>
      <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>To change providers, folder paths, or thresholds, edit the local <code>.env</code> file and restart the backend. The web UI does not pretend to save restart-bound settings.</p>
    </>}
  </div>;
}
