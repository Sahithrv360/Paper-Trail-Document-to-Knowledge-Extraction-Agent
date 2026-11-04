import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, FileText, Image, RefreshCw, AlertCircle, Network } from 'lucide-react';
import { aiService } from '../services/aiService';

export default function DocumentInspectorPage() {
  const { docId } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState(null);
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [imageUrl, setImageUrl] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [imageError, setImageError] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [recordDraft, setRecordDraft] = useState('');
  const [reviewMessage, setReviewMessage] = useState('');
  const [savingCorrection, setSavingCorrection] = useState(false);

  async function load() {
    setLoading(true); setError('');
    try {
      const graphData = await aiService.getKnowledgeGraph(true);
      let detail;
      try {
        detail = await aiService.getDocument(docId);
      } catch (documentError) {
        const matches = graphData.nodes.filter(item => String(item.id) === String(docId)
          || (item.support || []).some(support => String(support.document_id) === String(docId)));
        const node = matches.find(item => item.type === 'SyntheticEmail')
          || matches.find(item => item.type === 'ConversationSummary')
          || matches.find(item => item.type === 'Invoice')
          || matches[0];
        if (!node) throw documentError;
        detail = {
          id: docId, filename: node.label, file_type: node.type, status: 'Graph source',
          isGraphNode: true, graph_type: node.type, invoice_meta: node.data || {},
          review_status: node.data?.review_status || null,
          validation_status: node.data?.validation_status || null
        };
      }
      setDoc(detail); setGraph(graphData);
    } catch (err) { setError(err.message || 'Could not load document from the backend.'); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, [docId]);
  useEffect(() => {
    let cancelled = false;
    if (!doc?.id) return undefined;
    setImageUrl('');
    setImageError('');
    if (doc.isGraphNode) {
      setImageUrl('');
      setImageError('This graph source is an email or summary record and has no document page image.');
      return undefined;
    }
    aiService.getDocumentImage(doc.id).then(url => {
      if (cancelled) URL.revokeObjectURL(url); else setImageUrl(url);
    }).catch(err => setImageError(err.message || 'Source image preview is unavailable.'));
    return () => { cancelled = true; if (imageUrl) URL.revokeObjectURL(imageUrl); };
  }, [doc?.id]);

  if (loading) return <div className="card" style={{ padding: 28 }}>Loading document and graph evidence…</div>;
  if (error || !doc) return <div className="card" role="alert" style={{ padding: 24, color: '#991b1b' }}><AlertCircle size={18} /> {error || 'Document not found.'}<button className="btn btn-outline btn-sm" onClick={load} style={{ marginLeft: 12 }}><RefreshCw size={14} /> Retry</button><button className="btn btn-outline btn-sm" onClick={() => navigate('/documents')} style={{ marginLeft: 8 }}>Documents</button></div>;

  const record = doc.invoice_meta || {};
  const evidence = record.field_evidence || record.evidence || {};
  const nodes = graph.nodes.filter(node => String(node.id) === String(doc.id)
    || (node.support || []).some(item => item.document_id === doc.id));
  const nodeIds = new Set(nodes.map(node => node.id));
  const edges = graph.edges.filter(edge => (edge.support || []).some(item => item.document_id === doc.id)
    || (nodeIds.has(edge.source) && nodeIds.has(edge.target)));
  const invoiceFields = Object.entries(record).filter(([key, value]) => !['provenance', 'field_evidence', 'evidence', 'findings', 'items', 'body', 'summary_text'].includes(key) && value !== null && value !== undefined && typeof value !== 'object');
  const saveCorrection = async () => {
    setReviewMessage('');
    let correctedRecord;
    try { correctedRecord = JSON.parse(recordDraft); }
    catch (_) { setReviewMessage('The edited record is not valid JSON.'); return; }
    setSavingCorrection(true);
    try {
      await aiService.correctDocument(doc.id, correctedRecord);
      setIsEditing(false);
      setReviewMessage('Correction saved to the backend review ledger.');
      await load();
    } catch (err) { setReviewMessage(err.message || 'Could not save correction.'); }
    finally { setSavingCorrection(false); }
  };

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
    <header style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}><div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}><button className="btn btn-outline btn-sm" onClick={() => navigate('/documents')}><ArrowLeft size={14} /> Documents</button><div><span className={`badge ${doc.status === 'Completed' ? 'badge-green' : 'badge-amber'}`}>{doc.status}</span><h1 style={{ fontSize: '1.5rem', margin: '7px 0 2px' }}>{doc.filename}</h1><span style={{ color: 'var(--text-muted)', fontSize: 13 }}>ID: {doc.id}</span></div></div><button className="btn btn-outline btn-sm" onClick={() => navigate('/graph')}><Network size={14} /> Open graph</button></header>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 16 }}>
      <section className="card" style={{ padding: 18 }}><div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}><h2 style={{ marginTop: 0, fontSize: 17 }}><FileText size={17} /> {doc.isGraphNode ? 'Source record details' : 'Extraction record'}</h2>{!doc.isGraphNode && !isEditing && <button className="btn btn-outline btn-sm" onClick={() => { setRecordDraft(JSON.stringify(record, null, 2)); setIsEditing(true); }}>Correct record</button>}</div><p style={{ color: 'var(--text-secondary)' }}>Review: <strong>{doc.review_status || 'Not recorded'}</strong><br />Validation: <strong>{doc.validation_status || 'Not recorded'}</strong><br />Confidence: <strong>{doc.confidence_score == null ? 'Not available' : `${Math.round(doc.confidence_score * 100)}% (${doc.confidence_basis || 'basis not recorded'})`}</strong><br />Provider: <strong>{record.provenance?.provider || 'Not recorded'}</strong></p>{reviewMessage && <p role="status" style={{ color: reviewMessage.startsWith('Correction saved') ? '#047857' : '#b91c1c' }}>{reviewMessage}</p>}{isEditing && <div><p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Edit the extraction JSON. The backend validates and records the correction against the current extraction version.</p><textarea value={recordDraft} onChange={event => setRecordDraft(event.target.value)} rows={16} spellCheck={false} style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 12, padding: 12, border: '1px solid var(--border-light)', borderRadius: 8 }} /><div style={{ display: 'flex', gap: 8, marginTop: 10 }}><button className="btn btn-primary btn-sm" onClick={saveCorrection} disabled={savingCorrection}>{savingCorrection ? 'Saving…' : 'Save correction'}</button><button className="btn btn-outline btn-sm" onClick={() => setIsEditing(false)} disabled={savingCorrection}>Cancel</button></div></div>}</section>
      <section className="card" style={{ padding: 18 }}><h2 style={{ marginTop: 0, fontSize: 17 }}>{doc.isGraphNode ? (doc.graph_type === 'SyntheticEmail' ? 'Email record' : 'Graph source') : 'Source file'}</h2><p>{doc.filename} · {doc.file_type}</p>{doc.isGraphNode ? <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55, overflowWrap: 'anywhere' }}>{record.body || record.summary_text || 'No message body or summary is stored on this graph source.'}</div> : imageUrl ? <img src={imageUrl} alt={`Source page for ${doc.filename}`} style={{ maxWidth: '100%', maxHeight: 520, objectFit: 'contain', border: '1px solid var(--border-light)', borderRadius: 8 }} /> : <p style={{ color: imageError ? '#b91c1c' : 'var(--text-muted)' }}>{imageError || 'Loading source preview…'}</p>}{imageError && !doc.isGraphNode && <button className="btn btn-outline btn-sm" onClick={() => { setImageError(''); aiService.getDocumentImage(doc.id).then(setImageUrl).catch(err => setImageError(err.message)); }}><Image size={14} /> Retry preview</button>}</section>
    </div>
    <section className="card" style={{ padding: 18 }}><h2 style={{ marginTop: 0, fontSize: 17 }}>Extracted fields</h2>{invoiceFields.length ? <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 10 }}>{invoiceFields.map(([key, value]) => <div key={key} style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}><div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{key.replaceAll('_', ' ')}</div><strong>{String(value)}</strong></div>)}</div> : <p>No scalar fields are present in the extraction record.</p>}{Array.isArray(record.items) && record.items.length > 0 && <div style={{ marginTop: 18 }}><h3>Line items recorded ({record.items.length})</h3>{record.items.map((item, index) => <pre key={index} style={{ whiteSpace: 'pre-wrap', background: '#f8fafc', padding: 12, borderRadius: 8 }}>{JSON.stringify(item, null, 2)}</pre>)}</div>}</section>
    <section className="card" style={{ padding: 18 }}><h2 style={{ marginTop: 0, fontSize: 17 }}>Field evidence</h2>{Object.keys(evidence).length ? Object.entries(evidence).map(([key, item]) => <blockquote key={key} style={{ margin: '10px 0', borderLeft: '3px solid #60a5fa', padding: '8px 12px', background: '#f8fafc' }}><strong>{key}</strong><div>{item?.quote || item?.raw_text || 'The record has no saved quotation for this field.'}</div>{item?.page ? <small>Page {item.page}</small> : null}</blockquote>) : <p>No field-level quotations are stored for this document.</p>}</section>
    <section className="card" style={{ padding: 18 }}><h2 style={{ marginTop: 0, fontSize: 17 }}>Knowledge graph support</h2><p>{nodes.length} supported nodes · {edges.length} linked relationships</p><div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{nodes.map(node => <span key={node.id} className="badge badge-blue">{node.type}: {node.label}</span>)}</div>{edges.map(edge => <div key={edge.id} style={{ padding: '7px 0', color: 'var(--text-secondary)' }}>{edge.source} — <strong>{edge.type}</strong> → {edge.target}</div>)}</section>
  </div>;
}
