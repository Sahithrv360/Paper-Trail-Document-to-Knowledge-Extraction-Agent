import React, { useEffect, useState } from 'react';
import { AlertTriangle, FileText, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { aiService } from '../services/aiService';

export default function ContradictionsPage() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  async function load() {
    setLoading(true); setError('');
    try { setItems(await aiService.getContradictions()); }
    catch (err) { setError(err.message || 'Could not load potential conflicts.'); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
    <header style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
      <div><span className="badge badge-amber">Source comparison</span><h1 style={{ fontSize: '1.75rem', margin: '8px 0 4px' }}>Potential invoice conflicts</h1>
        <p style={{ margin: 0, color: 'var(--text-secondary)' }}>Only repeated invoice numbers with differing recorded dates, currencies, or totals are listed. Review source documents before deciding whether a difference is an error.</p></div>
      <button className="btn btn-outline btn-sm" onClick={load} disabled={loading}><RefreshCw size={15} /> Refresh</button>
    </header>
    {error && <div className="card" role="alert" style={{ padding: 16, color: '#b91c1c' }}>{error}</div>}
    {loading ? <div className="card" style={{ padding: 24 }}>Checking current graph records…</div> : items.length ? items.map(item => <article className="card" key={item.invoice_number} style={{ padding: 20 }}>
      <h2 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 18 }}><AlertTriangle size={18} color="#d97706" /> Invoice {item.invoice_number}</h2>
      <p>Recorded values differ for: <strong>{item.properties.join(', ')}</strong>.</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{item.documents.map(id => <Link key={id} className="btn btn-outline btn-sm" to={`/documents/inspector/${encodeURIComponent(id)}`}><FileText size={14} /> Inspect source</Link>)}</div>
    </article>) : !error && <div className="card" style={{ padding: 28, color: 'var(--text-secondary)' }}>No repeated invoice numbers with differing tracked fields were found. The system does not infer conflicts from unrelated invoices.</div>}
  </div>;
}
