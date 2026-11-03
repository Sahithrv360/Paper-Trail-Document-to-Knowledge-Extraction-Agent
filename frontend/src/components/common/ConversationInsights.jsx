import React from 'react';
import { Mail, Users, MessageSquareText } from 'lucide-react';

export default function ConversationInsights({ threads = [], compact = false }) {
  const visibleThreads = compact ? threads.slice(0, 3) : threads;
  const emailCount = threads.reduce((count, thread) => count + thread.emails.length, 0);
  return (
    <section className="card" style={{ padding: '22px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: '1.1rem', fontWeight: 800, margin: 0 }}>Vendor and customer conversations</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '.82rem', margin: '5px 0 0' }}>Per-invoice thread summaries with the supporting message sequence.</p>
        </div>
        <span className="badge badge-amber">Synthetic demo data · fictional</span>
      </div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <span className="badge badge-blue"><MessageSquareText size={13} /> {threads.length} invoice threads</span>
        <span className="badge badge-purple"><Mail size={13} /> {emailCount} messages</span>
      </div>
      {visibleThreads.length ? <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14 }}>
        {visibleThreads.map(thread => (
          <article key={thread.invoiceNumber} style={{ border: '1px solid var(--border-light)', borderRadius: 12, padding: 16, background: '#fff' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
              <div><div style={{ fontWeight: 800, fontSize: '1rem' }}>Invoice {thread.invoiceNumber}</div>
                <div style={{ color: 'var(--text-muted)', fontSize: '.78rem', marginTop: 4 }}><Users size={13} style={{ verticalAlign: 'middle' }} /> {thread.vendor} → {thread.customer}</div>
              </div>
              <span className="badge badge-gray">{thread.emails.length} emails</span>
            </div>
            {thread.summary?.data?.summary_text && <p style={{ fontSize: '.86rem', lineHeight: 1.55, margin: '14px 0', color: 'var(--text-secondary)' }}>{thread.summary.data.summary_text}</p>}
            {!thread.summary?.data?.summary_text && <p style={{ fontSize: '.84rem', color: 'var(--text-muted)', margin: '12px 0' }}>No conversation summary is available for this invoice.</p>}
            <details>
              <summary style={{ cursor: 'pointer', color: '#2563eb', fontSize: '.82rem', fontWeight: 700 }}>View conversation timeline</summary>
              <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                {thread.emails.map((email, index) => <div key={email.id} style={{ borderLeft: '2px solid #cbd5e1', paddingLeft: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: '.82rem' }}>{index + 1}. {email.data?.subject || 'Email'} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>{email.data?.sent_at || ''}</span></div>
                  <div style={{ fontSize: '.76rem', color: 'var(--text-muted)', marginTop: 3 }}>{email.data?.sender_name || email.data?.sender || 'Sender'} → {email.data?.recipient_name || email.data?.recipient || 'Recipient'}</div>
                  <p style={{ fontSize: '.8rem', lineHeight: 1.45, margin: '5px 0 0' }}>{email.data?.summary_text || email.data?.body || 'Message content unavailable.'}</p>
                </div>)}
              </div>
            </details>
          </article>
        ))}
      </div> : <p style={{ color: 'var(--text-muted)' }}>No conversation threads are present in the current graph.</p>}
    </section>
  );
}
