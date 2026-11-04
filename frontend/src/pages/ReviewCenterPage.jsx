import React, { useEffect, useState } from 'react';
import { AlertOctagon, CheckCircle2, ExternalLink, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { aiService } from '../services/aiService';

export default function ReviewCenterPage() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState('');
  const load = async () => {
    setLoading(true); setError('');
    try { setItems(await aiService.getReviewItems()); }
    catch (err) { setError(err.message || 'Could not load review queue.'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);
  const approve = async item => {
    setBusyId(item.id); setError('');
    const result = await aiService.approveReviewItem(item.id);
    if (result?.success === false || result?.error) setError(result.error || 'Review action failed.');
    else await load();
    setBusyId('');
  };

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
    <header style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
      <div><span className="badge badge-amber">Human review</span><h1 style={{ fontSize: '1.75rem', margin: '8px 0 4px' }}>Review Center</h1>
        <p style={{ margin: 0, color: 'var(--text-secondary)' }}>Review is applied to the current extraction version in the backend ledger. Approval records that review decision.</p></div>
      <button className="btn btn-outline btn-sm" onClick={load} disabled={loading}><RefreshCw size={15} /> Refresh</button>
    </header>
    {error && <div className="card" role="alert" style={{ padding: 16, color: '#b91c1c' }}>{error}</div>}
    {loading ? <div className="card" style={{ padding: 24 }}>Loading current review queue…</div> : items.length ? items.map(item => <article className="card" key={item.id} style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}><div><h2 style={{ margin: 0, fontSize: 18 }}>{item.title}</h2><p style={{ color: 'var(--text-secondary)' }}>{item.description || item.category}</p><div className="badge badge-amber">{item.category}</div></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Link className="btn btn-outline btn-sm" to={`/documents/inspector/${encodeURIComponent(item.id)}`}><ExternalLink size={14} /> Inspect source</Link><button className="btn btn-primary btn-sm" onClick={() => approve(item)} disabled={busyId === item.id}><CheckCircle2 size={15} /> {busyId === item.id ? 'Saving…' : 'Approve extraction'}</button></div></div>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 0 }}>This action approves the extraction version currently shown in the Inspector. Use the Inspector to correct field values before approving if needed.</p>
    </article>) : !error && <div className="card" style={{ padding: 28, textAlign: 'center' }}><AlertOctagon size={32} color="#059669" /><h2>No documents currently need review</h2><p style={{ color: 'var(--text-secondary)' }}>The backend has no active extraction marked needs_review.</p></div>}
  </div>;
}
