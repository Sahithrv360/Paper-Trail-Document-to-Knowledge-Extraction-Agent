import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowRight, FileText, RefreshCw, AlertCircle, CheckCircle2 } from 'lucide-react';
import { aiService } from '../services/aiService';

export default function DocumentProcessingPage() {
  const { docId } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const load = async () => {
    setLoading(true); setError('');
    try {
      if (!docId) throw new Error('No document was selected. Open this page from a document record.');
      setDoc(await aiService.getDocument(docId));
    } catch (err) { setError(err.message || 'Could not load document processing details.'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [docId]);

  if (loading) return <div className="card" style={{ padding: 28 }}>Loading extraction record…</div>;
  if (error || !doc) return <div className="card" role="alert" style={{ padding: 24, color: '#b91c1c' }}><AlertCircle size={18} /> {error || 'Document record not found.'}<button className="btn btn-outline btn-sm" onClick={load} style={{ marginLeft: 12 }}><RefreshCw size={14} /> Retry</button></div>;

  const record = doc.invoice_meta || {};
  const provenance = record.provenance || {};
  const fields = Object.entries(record).filter(([key, value]) => !['provenance', 'field_evidence', 'evidence', 'findings'].includes(key) && value !== null && value !== undefined && typeof value !== 'object');
  const evidence = Object.entries(record.field_evidence || record.evidence || {});
  const statusClass = doc.status === 'Completed' ? 'badge-green' : doc.status === 'Failed' ? 'badge-red' : 'badge-amber';

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 1100, margin: '0 auto' }}>
    <header style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}><div><span className={`badge ${statusClass}`}>{doc.status}</span><h1 style={{ fontSize: '1.75rem', margin: '8px 0 4px' }}>{doc.filename}</h1><p style={{ margin: 0, color: 'var(--text-secondary)' }}>Live extraction ledger details for document {doc.id}</p></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><button className="btn btn-outline btn-sm" onClick={load}><RefreshCw size={14} /> Refresh</button><button className="btn btn-primary btn-sm" onClick={() => navigate(`/documents/inspector/${encodeURIComponent(doc.id)}`)}>Open Inspector <ArrowRight size={14} /></button></div></header>
    <section className="card" style={{ padding: 20 }}><h2 style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 0 }}><FileText size={18} /> Active extraction</h2><div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 16 }}>
      {[['State', record.state || doc.status], ['Review status', record.review_status || doc.review_status || 'Not recorded'], ['Validation', record.validation_status || doc.validation_status || 'Not recorded'], ['Confidence', doc.confidence_score == null ? 'Not available' : `${Math.round(doc.confidence_score * 100)}%`], ['Provider', provenance.provider || 'Not recorded'], ['Model revision', provenance.model_revision || 'Not recorded'], ['Evidence fields', evidence.length], ['Extraction versions', doc.version_history?.length || 0]].map(([name, value]) => <div key={name}><div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{name}</div><strong>{String(value)}</strong></div>)}
    </div></section>
    <section className="card" style={{ padding: 20 }}><h2 style={{ marginTop: 0 }}>Extracted record fields</h2>{fields.length ? <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12 }}>{fields.map(([key, value]) => <div key={key} style={{ padding: 12, background: '#f8fafc', borderRadius: 8 }}><div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{key.replaceAll('_', ' ')}</div><strong>{String(value)}</strong></div>)}</div> : <p style={{ color: 'var(--text-secondary)' }}>The extraction record has no scalar fields to show.</p>}</section>
    <section className="card" style={{ padding: 20 }}><h2 style={{ marginTop: 0 }}>Evidence and version history</h2>{evidence.length ? evidence.map(([key, item]) => <p key={key}><strong>{key}:</strong> {item?.quote || item?.raw_text || 'Evidence text unavailable.'}</p>) : <p style={{ color: 'var(--text-secondary)' }}>No field quotations are stored for this extraction.</p>}
      {(doc.version_history || []).map(version => <div key={version.extraction_id} style={{ borderTop: '1px solid var(--border-light)', padding: '10px 0', fontSize: 13 }}><CheckCircle2 size={14} /> Version {version.version} · {version.state} · {version.review_status || 'unreviewed'} · {version.created_at}</div>)}
    </section>
  </div>;
}
