import React from 'react';
import { FolderOpen, X } from 'lucide-react';

export default function UploadModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(event) => event.stopPropagation()} style={{ padding: '28px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '18px' }}>
          <div>
            <h3 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Add documents</h3>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
              The connected Paper Trail API ingests files from its watched folder.
            </p>
          </div>
          <button className="btn btn-outline btn-sm" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="card" style={{ padding: '24px', display: 'flex', alignItems: 'center', gap: '16px' }}>
          <FolderOpen size={32} color="#2563eb" />
          <div>
            <div style={{ fontWeight: 700 }}>Place files in the backend watched folder</div>
            <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginTop: '6px' }}>
              Supported formats include PDF and common image files. The documents list refreshes automatically as the backend processes them.
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '20px' }}>
          <button className="btn btn-primary" onClick={onClose}>Got it</button>
        </div>
      </div>
    </div>
  );
}
