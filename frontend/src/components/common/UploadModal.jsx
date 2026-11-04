import React, { useEffect, useState } from 'react';
import { FolderOpen, X, AlertCircle, CheckCircle2, LoaderCircle } from 'lucide-react';
import { aiService } from '../../services/aiService';

export default function UploadModal({ isOpen, onClose }) {
  const [runtime, setRuntime] = useState(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState('');
  const [results, setResults] = useState(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setError('');
    aiService.getWatcherStatus().then(setRuntime).catch(err => setError(err.message));
  }, [isOpen]);

  const uploadSelection = async event => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;
    const allowed = new Set(runtime?.supported_extensions || []);
    const candidates = files.filter(file => allowed.has(`.${file.name.split('.').pop().toLowerCase()}`));
    const skipped = files.length - candidates.length;
    const maxBytes = (runtime?.max_file_mb || 25) * 1024 * 1024;
    const oversized = candidates.filter(file => file.size > maxBytes);
    const queue = candidates.filter(file => file.size <= maxBytes);
    const uploaded = [];
    const failed = oversized.map(file => `${file.webkitRelativePath || file.name}: exceeds ${runtime?.max_file_mb || 25} MB`);

    setUploading(true);
    setError('');
    setResults(null);
    try {
      for (const [index, file] of queue.entries()) {
        setProgress(`Uploading ${index + 1} of ${queue.length}: ${file.webkitRelativePath || file.name}`);
        try {
          const result = await aiService.uploadWatchedFile(file);
          uploaded.push(result.filename);
        } catch (uploadError) {
          failed.push(`${file.webkitRelativePath || file.name}: ${uploadError.message}`);
        }
      }
      setResults({ uploaded, failed, skipped });
      if (uploaded.length) window.dispatchEvent(new CustomEvent('papertrail:data-changed'));
    } finally {
      setProgress('');
      setUploading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={() => !uploading && onClose()}>
      <div className="modal-content" onClick={event => event.stopPropagation()} style={{ padding: 28, maxWidth: 650 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 18 }}>
          <div>
            <h3 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0 }}>Upload a folder</h3>
            <p style={{ fontSize: '.875rem', color: 'var(--text-secondary)', margin: '6px 0 0' }}>
              Supported files are copied into the backend watched folder, where the watcher can register them.
            </p>
          </div>
          <button className="btn btn-outline btn-sm" onClick={onClose} disabled={uploading} aria-label="Close"><X size={16} /></button>
        </div>

        <div className="card" style={{ padding: 20 }}>
          <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <FolderOpen size={30} color="#2563eb" />
            <div style={{ flex: 1 }}>
              <strong>Destination: watched folder</strong>
              <p style={{ fontSize: '.84rem', color: 'var(--text-secondary)', margin: '6px 0 12px', overflowWrap: 'anywhere' }}>
                {runtime ? runtime.watch_folder : 'Loading watch folder settings…'}
              </p>
              <p style={{ fontSize: '.8rem', color: 'var(--text-muted)', margin: '0 0 14px' }}>
                {runtime ? `Types: ${(runtime.supported_extensions || []).join(', ')} · Maximum ${runtime.max_file_mb || 25} MB per file. Duplicate names receive a number suffix.` : 'Reading supported types and size limits…'}
              </p>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <label className={`btn btn-primary ${uploading || !runtime ? 'disabled' : ''}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: uploading || !runtime ? 'not-allowed' : 'pointer' }}>
                  {uploading ? <LoaderCircle size={16} className="spin" /> : <FolderOpen size={16} />}
                  {uploading ? 'Uploading…' : 'Choose folder'}
                  <input type="file" multiple webkitdirectory="" directory="" onChange={uploadSelection} disabled={uploading || !runtime} style={{ display: 'none' }} />
                </label>
                <label className={`btn btn-outline ${uploading || !runtime ? 'disabled' : ''}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: uploading || !runtime ? 'not-allowed' : 'pointer' }}>
                  Choose files
                  <input type="file" multiple accept={(runtime?.supported_extensions || []).join(',')} onChange={uploadSelection} disabled={uploading || !runtime} style={{ display: 'none' }} />
                </label>
              </div>
              {progress && <p role="status" style={{ margin: '12px 0 0', fontSize: 13 }}>{progress}</p>}
            </div>
          </div>
        </div>

        {error && <div role="alert" style={{ color: '#b91c1c', marginTop: 12, fontSize: 13 }}><AlertCircle size={14} /> {error}</div>}
        {results && <div role="status" className="card" style={{ marginTop: 14, padding: 14 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 700, color: '#047857' }}><CheckCircle2 size={16} /> {results.uploaded.length} file(s) copied into the watched folder</div>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: '6px 0' }}>
            {runtime?.watcher_process?.includes('enabled in review server')
              ? 'The integrated watcher will register and process supported files.'
              : 'The separate watcher will register supported files on its next scan.'}
          </p>
          {results.skipped > 0 && <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: '4px 0' }}>{results.skipped} unsupported file(s) skipped.</p>}
          {results.failed.length > 0 && <div style={{ color: '#b91c1c', fontSize: 12, marginTop: 8 }}>{results.failed.slice(0, 6).map((item, index) => <div key={index}>{item}</div>)}{results.failed.length > 6 && <div>And {results.failed.length - 6} more failed files.</div>}</div>}
        </div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}><button className="btn btn-outline" onClick={onClose} disabled={uploading}>Close</button></div>
      </div>
    </div>
  );
}
