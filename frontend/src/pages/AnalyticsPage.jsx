import React, { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { FileText, Layers, Link2, ShieldCheck, Sparkles } from 'lucide-react';
import KpiCard from '../components/common/KpiCard';
import { aiService } from '../services/aiService';

export default function AnalyticsPage() {
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    aiService.getAnalytics().then(setAnalytics).catch(error => console.error('Failed to load analytics:', error)).finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="card" style={{ padding: 50, textAlign: 'center' }}><Sparkles className="spin" /> Loading graph analytics…</div>;

  const kpis = analytics?.kpis || {};
  const types = (analytics?.entities_by_type || []).filter(row => row.count > 0);
  const confidence = analytics?.confidence_distribution || [];
  const colors = ['#2563eb', '#10b981', '#8b5cf6', '#f59e0b', '#0f9f9a', '#c24178'];
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
    <header>
      <span className="badge badge-blue">Current graph snapshot</span>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 800, margin: '8px 0 4px' }}>Knowledge Analytics</h1>
      <p style={{ color: 'var(--text-secondary)', margin: 0 }}>Counts are derived from records currently available in the local graph. Historical throughput is not tracked yet.</p>
    </header>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 16 }}>
      <KpiCard title="Graph documents" value={kpis.documents_processed || 0} changeText="Current records" icon={FileText} color="blue" />
      <KpiCard title="Graph nodes" value={kpis.entities_extracted || 0} changeText="All node types" icon={Layers} color="green" />
      <KpiCard title="Relationships" value={kpis.relationships_discovered || 0} changeText="Current graph edges" icon={Link2} color="purple" />
      <KpiCard title="Invoice facts" value={kpis.facts_extracted || 0} changeText="Structured invoice fields" icon={ShieldCheck} color="amber" />
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 18 }}>
      <section className="card" style={{ padding: 22 }}>
        <h2 style={{ fontSize: '1.05rem', fontWeight: 750, margin: '0 0 4px' }}>Graph nodes by type</h2>
        <p style={{ color: 'var(--text-muted)', fontSize: '.82rem', margin: '0 0 16px' }}>Includes invoices, emails, summaries, and other stored node types.</p>
        {types.length ? <div style={{ height: 300 }}><ResponsiveContainer width="100%" height="100%"><BarChart data={types} layout="vertical" margin={{ left: 12, right: 14 }}><XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="name" width={130} /><Tooltip /><Bar dataKey="count" radius={[0, 5, 5, 0]}>{types.map((row, index) => <Cell key={row.name} fill={colors[index % colors.length]} />)}</Bar></BarChart></ResponsiveContainer></div> : <p style={{ color: 'var(--text-muted)' }}>No graph nodes are available.</p>}
      </section>
      <section className="card" style={{ padding: 22 }}>
        <h2 style={{ fontSize: '1.05rem', fontWeight: 750, margin: '0 0 4px' }}>Estimated extraction quality scores</h2>
        <p style={{ color: 'var(--text-muted)', fontSize: '.82rem', margin: '0 0 16px' }}>Rule-based scores combine core-field coverage, source-quote matches, and validation results. They are not calibrated probabilities.</p>
        {confidence.length ? <div style={{ height: 300 }}><ResponsiveContainer width="100%" height="100%"><BarChart data={confidence}><XAxis dataKey="range" /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="count" fill="#0f9f9a" radius={[5, 5, 0, 0]} /></BarChart></ResponsiveContainer></div> : <div style={{ padding: '50px 10px', textAlign: 'center', color: 'var(--text-muted)' }}>No extracted documents have scores yet.</div>}
      </section>
    </div>
    <section className="card" style={{ padding: 18, color: 'var(--text-secondary)', fontSize: '.88rem' }}>
      <strong>Data limitation:</strong> the current backend exposes a graph snapshot, not timestamped event history. The dashboard therefore does not estimate “today” totals or draw a fabricated extraction timeline.
    </section>
  </div>;
}
