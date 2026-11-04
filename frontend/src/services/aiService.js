class AIService {
  constructor() {
    this.apiBase = '/api';
    this.apiError = null;
    this.graphCache = null;
    this.graphCacheAt = 0;
    this.graphPromise = null;
    this.documentsCache = null;
    this.documentsCacheAt = 0;
    this.documentsPromise = null;
    this.analyticsCache = null;
    this.analyticsCacheAt = 0;
    this.analyticsPromise = null;
    this.conversationsCache = null;
    this.conversationsCacheAt = 0;
    this.conversationsPromise = null;

    if (typeof window !== 'undefined') {
      window.addEventListener('papertrail:data-changed', () => {
        this.graphCache = null;
        this.documentsCache = null;
        this.analyticsCache = null;
        this.conversationsCache = null;
      });
    }

    this.documents = [];
    this.entities = [];
    this.relationships = [];
    this.facts = [];
    this.evidence = [];
    this.reviewItems = [];
    this.contradictions = [];
  }

  getReviewToken() {
    if (typeof window === 'undefined') return '';
    const fromHash = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token');
    if (fromHash) {
      window.sessionStorage.setItem('papertrail_review_token', fromHash);
      return fromHash;
    }
    return window.sessionStorage.getItem('papertrail_review_token') || '';
  }

  async request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = this.getReviewToken();
    if (token) headers.set('X-Review-Token', token);
    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    let response;
    try { response = await fetch(`${this.apiBase}${path}`, { ...options, headers }); }
    catch (error) {
      this.apiError = error.message || 'Backend is unreachable';
      window.dispatchEvent(new CustomEvent('papertrail:api-error', { detail: this.apiError }));
      throw error;
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      const message = detail.error || `Paper Trail API returned ${response.status}`;
      this.apiError = message;
      window.dispatchEvent(new CustomEvent('papertrail:api-error', { detail: message }));
      throw new Error(message);
    }
    this.apiError = null;
    if (path === '/status') window.dispatchEvent(new CustomEvent('papertrail:api-error', { detail: '' }));
    return response;
  }

  documentFromApi(row) {
    const file = row.file || 'Document';
    const fileType = file.includes('.') ? file.split('.').pop().toUpperCase() : 'FILE';
    const status = row.file_status === 'failed' ? 'Failed'
      : row.completion_status === 'complete' ? 'Completed'
      : row.file_status === 'complete' && row.extraction_state ? 'Needs Review'
      : row.file_status === 'complete' ? 'Awaiting Extraction' : 'Processing';
    const isReview = row.review_status === 'needs_review' || row.validation_status === 'ambiguous_review';
    return {
      id: row.document_id,
      filename: file,
      file_type: fileType,
      file_size: null,
      uploaded_at: row.last_synced_at || 'Local watch folder',
      status,
      pages_count: null,
      chunks_count: null,
      entities_count: 0,
      relationships_count: 0,
      facts_count: 0,
      confidence_score: row.confidence_score ?? null,
      confidence_basis: row.confidence_basis || null,
      completion_threshold: row.completion_threshold ?? 0.8,
      needs_review_count: isReview ? 1 : 0,
      summary: row.last_error || row.validation_status || row.extraction_state || 'Waiting for extraction',
      validation_status: row.validation_status,
      review_status: row.review_status,
      text_content: ''
    };
  }

  async queryKnowledge(query, knowledgeBase = null, mode = 'hybrid', onProgress = null, includeUnreviewed = false, history = [], conversationId = null) {
    return this.answerKnowledgeQuery(query, knowledgeBase, mode, onProgress, includeUnreviewed, history, conversationId);
  }

  async answerKnowledgeQuery(query, knowledgeBase = null, mode = 'hybrid', onProgress = null, includeUnreviewed = false, history = [], conversationId = null) {
    try {
      const requestId = mode === 'agent' ? crypto.randomUUID() : undefined;
      let polling = Boolean(requestId && onProgress);
      const pollProgress = async () => {
        while (polling) {
          try {
            const statusResponse = await this.request(`/agent-runs/${requestId}`);
            const status = await statusResponse.json();
            onProgress(status);
            if (status.status === 'completed' || status.status === 'failed') break;
          } catch (_) { /* The query request still carries the authoritative error. */ }
          await new Promise(resolve => setTimeout(resolve, 350));
        }
      };
      const progressPoll = polling ? pollProgress() : null;
      const responsePromise = this.request('/graph-rag', {
        method: 'POST',
        body: JSON.stringify({ question: query, generate: mode === 'agent', limit: 8, mode,
          include_unreviewed: includeUnreviewed, request_id: requestId,
          history: Array.isArray(history) ? history.slice(-8).map(({ role, content }) => ({
            role: role === 'assistant' ? 'assistant' : 'user',
            content: String(content || '').slice(0, 1200)
          })) : [], conversation_id: conversationId })
      });
      let res;
      try {
        res = await responsePromise;
      } finally {
        polling = false;
        if (progressPoll) await progressPoll;
      }
      const data = await res.json();
      const links = data.links || data.matches?.flatMap(match => match.links || []) || [];
      const fullGraph = await this.getKnowledgeGraph();
      const sourceIds = new Set((data.sources || []).map(source => source.id));
      const linkedNodeIds = new Set(links.flatMap(link => link.source_ids || []));
      const citedEdgeIds = new Set(links.flatMap(link => link.edge_ids || []));
      const directEdges = fullGraph.edges.filter(edge => citedEdgeIds.has(edge.id));
      const connectedEdges = fullGraph.edges.filter(edge =>
        linkedNodeIds.has(edge.source) && linkedNodeIds.has(edge.target));
      const edgesById = new Map([...directEdges, ...connectedEdges].map(edge => [edge.id, edge]));
      const candidateEdges = [...edgesById.values()];
      const candidateNodeIds = [...new Set([
        ...sourceIds, ...linkedNodeIds,
        ...candidateEdges.flatMap(edge => [edge.source, edge.target])
      ])];
      const fullNodesById = new Map(fullGraph.nodes.map(node => [node.id, node]));
      const evidenceNodes = candidateNodeIds.slice(0, 24)
        .map(id => fullNodesById.get(id)).filter(Boolean)
        .map(node => ({ ...node, isEvidenceSource: sourceIds.has(node.id) }));
      const evidenceNodeIds = new Set(evidenceNodes.map(node => node.id));
      const evidenceEdges = candidateEdges.filter(edge =>
        evidenceNodeIds.has(edge.source) && evidenceNodeIds.has(edge.target)).slice(0, 36);
      const citations = (data.evidence_cards || data.sources || []).map(source => ({
        source: source.source || source.filename || `${source.type || 'Source'} ${source.invoice_number || source.id}`,
        quote: source.quote || (source.invoice_number ? `Invoice number ${source.invoice_number}` : source.type),
        confidence: source.confidence ?? null,
        review_status: source.review_status || source.data?.review_status || null,
        synthetic: Boolean(source.synthetic),
        citation_ids: source.citation_ids || (source.citation_id ? [source.citation_id] : []),
        supporting_emails: source.supporting_emails || [],
        document_id: source.id
      }));
      const tableRows = evidenceNodes.flatMap(node => Object.entries(node.data || {})
        .filter(([key, value]) => !['id', 'synthetic', 'synthetic_notice', 'confidence', 'chunk_text', 'summary_text', 'body'].includes(key)
          && value !== null && value !== undefined && ['string', 'number', 'boolean'].includes(typeof value))
        .map(([key, value]) => ({ category: node.type, entity: node.label, property: key.replaceAll('_', ' '), value: String(value), confidence: null, source: node.data?.filename || node.type })));
      return {
        query,
        answer: data.answer || 'No matching graph facts were found.',
        confidence: data.confidence ?? null,
        pipeline_steps: data.pipeline_steps || [
          { step: 1, name: 'Question Understanding', description: 'Prepared the question for graph retrieval.', detail: query },
          { step: 2, name: 'Graph Retrieval', description: 'Searched the Paper Trail graph.', detail: `${data.retrieval_count ?? links.length} matches; backend ${data.retrieval_backend || 'sqlite'}.` },
          { step: 3, name: 'Evidence Collection', description: 'Collected source documents from retrieved graph facts.', detail: `${citations.length} source documents.` },
          { step: 4, name: 'Answer', description: 'Returned the backend’s evidence-grounded answer.', detail: data.answer_mode || 'local' }
        ],
        matched_entities: [],
        matched_facts: links,
        evidence_citations: citations,
        graph: { nodes: evidenceNodes, edges: evidenceEdges, meta: {
          retrieval_backend: data.retrieval_backend || 'sqlite+kg_agent',
          reviewed_only: data.reviewed_only,
          cited_nodes: sourceIds.size,
          omitted_nodes: Math.max(0, candidateNodeIds.length - evidenceNodes.length),
          omitted_edges: Math.max(0, candidateEdges.length - evidenceEdges.length)
        } },
        table: Array.isArray(data.table) ? data.table : tableRows,
        answer_mode: data.answer_mode,
        model_name: data.model_name,
        citations: data.citations || [],
        retrieval_backend: data.retrieval_backend,
        reviewed_only: data.reviewed_only
      };
    } catch (error) {
      return {
        query,
        answer: `Paper Trail API unavailable: ${error.message}`,
        confidence: 0,
        pipeline_steps: [],
        matched_entities: [], matched_facts: [], evidence_citations: [],
        graph: { nodes: [], edges: [] }, table: []
      };
    }

  }

  // --- REPOSITORY & API INTEGRATION METHODS ---

  async getDocuments(forceRefresh = false) {
    if (this.documentsPromise) return this.documentsPromise;
    if (!forceRefresh && this.documentsCache && Date.now() - this.documentsCacheAt < 10000) {
      return this.documentsCache;
    }
    const request = (async () => {
      try {
        const res = await this.request('/documents');
        const data = await res.json();
        const graph = await this.getKnowledgeGraph();
        const supportByDocument = new Map();
        for (const node of graph.nodes) for (const support of node.support || []) {
          const counts = supportByDocument.get(support.document_id) || { entities: 0, facts: 0 };
          if (node.type !== 'Document') counts.entities += 1;
          if (node.type === 'Invoice') counts.facts += ['invoice_number', 'invoice_date', 'total_amount', 'subtotal', 'tax_amount', 'currency']
            .filter(field => node.data?.[field] !== null && node.data?.[field] !== undefined).length;
          supportByDocument.set(support.document_id, counts);
        }
        this.documents = Array.isArray(data) ? data.map(row => {
          const document = this.documentFromApi(row);
          const counts = supportByDocument.get(row.document_id) || { entities: 0, facts: 0 };
          document.entities_count = counts.entities;
          document.facts_count = counts.facts;
          document.relationships_count = graph.edges.filter(edge =>
            edge.support?.some(item => item.document_id === row.document_id)).length;
          return document;
        }) : [];
        this.documentsCache = this.documents;
        this.documentsCacheAt = Date.now();
        return this.documentsCache;
      } catch (error) { throw error; }
    })();
    this.documentsPromise = request;
    try {
      return await request;
    } finally {
      if (this.documentsPromise === request) this.documentsPromise = null;
    }
  }

  async getWatcherStatus() {
    return (await this.request('/status').then(response => response.json()));
  }

  async uploadWatchedFile(file) {
    const form = new FormData();
    form.append('file', file, file.name);
    const response = await this.request('/upload', { method: 'POST', body: form });
    const result = await response.json();
    window.dispatchEvent(new CustomEvent('papertrail:data-changed'));
    return result;
  }

  async getDocument(docId) {
    try {
      const res = await this.request(`/documents/${encodeURIComponent(docId)}`);
      const payload = await res.json();
      const extraction = payload.extraction || {};
      const record = extraction.effective_record || extraction.record || {};
      let listRow = this.documents.find(d => d.id === docId);
      if (!listRow) listRow = (await this.getDocuments()).find(d => d.id === docId);
      const document = this.documentFromApi({
        document_id: docId,
        file: record.provenance?.source_file || listRow?.filename,
        file_status: listRow?.status === 'Failed' ? 'failed' : listRow?.status === 'Processing' ? 'queued' : 'complete',
        review_status: extraction.review_status,
        validation_status: record.validation_status,
        extraction_state: extraction.state
      });
      const evidenceMap = record.field_evidence || record.evidence || {};
      const evidenceText = Object.entries(evidenceMap)
        .map(([field, evidence]) => `${field}: ${evidence?.quote || ''}`).filter(Boolean).join('\n');
      document.invoice_meta = record;
      document.extraction_id = extraction.extraction_id;
      document.version_history = payload.versions || [];
      document.changes = payload.changes || [];
      document.evidence_count = Object.keys(evidenceMap).length;
      document.text_content = evidenceText;
      document.confidence_score = record.confidence_score ?? extraction.confidence_score ?? null;
      document.confidence_basis = extraction.confidence_basis || null;
      document.completion_threshold = extraction.completion_threshold ?? 0.8;
      document.status = extraction.completion_status === 'complete' ? 'Completed'
        : extraction.completion_status === 'needs_review' ? 'Needs Review' : document.status;
      document.pages = [{ page_number: 1, title: document.filename, text: evidenceText || 'OCR evidence is not available.' }];
      // Render scanned pages only when the user opens the image tab.
      document.image_url = '';
      return document;
    } catch (error) { throw error; }
  }

  async getDocumentImage(docId, page = 1) {
    const response = await this.request(
      `/documents/${encodeURIComponent(docId)}/pages/${encodeURIComponent(page)}.png`
    );
    return URL.createObjectURL(await response.blob());
  }

  async getEntities(filter = "All") {
    try {
      const graph = await this.getKnowledgeGraph();
      const documents = await this.getDocuments();
      const names = new Map(documents.map(doc => [doc.id, doc.filename]));
      this.entities = graph.nodes.filter(node => node.type !== 'Document').map(node => {
        const support = node.support?.[0] || {};
        return { id: node.id, name: node.label, type: node.type, properties: node.data,
          source_doc_id: support.document_id, source_doc_name: names.get(support.document_id),
          supporting_document_ids: [...new Set((node.support || []).map(item => item.document_id))],
          mentions_count: node.support?.length || 1,
          confidence: node.data?.confidence ?? null,
          status: ['approved', 'corrected'].includes(support.review_status) ? 'Verified' : 'Needs Review' };
      });
      return filter && filter !== 'All'
        ? this.entities.filter(entity => entity.type.toLowerCase() === filter.toLowerCase())
        : this.entities;
    } catch (error) { throw error; }
  }

  async getRelationships() {
    try {
      const graph = await this.getKnowledgeGraph();
      const names = new Map(graph.nodes.map(node => [node.id, node.label]));
      const documents = await this.getDocuments();
      const documentNames = new Map(documents.map(document => [document.id, document.filename]));
      this.relationships = graph.edges.map(edge => ({ id: edge.id,
        source_entity_id: edge.source, target_entity_id: edge.target,
        source_entity_name: names.get(edge.source) || edge.source,
        target_entity_name: names.get(edge.target) || edge.target,
        relation_type: edge.type, properties: edge.data, status: 'Supported', confidence: null,
        source_doc_id: edge.support?.[0]?.document_id,
        supporting_document_ids: [...new Set((edge.support || []).map(item => item.document_id))],
        source_doc_name: documentNames.get(edge.support?.[0]?.document_id) || '' }));
      return this.relationships;
    } catch (error) { throw error; }
  }

  async getFacts() {
    try {
      const graph = await this.getKnowledgeGraph();
      this.facts = graph.nodes.filter(node => node.type === 'Invoice' || node.type === 'LineItem').flatMap(node => {
        const fields = node.data || {};
        const factFields = node.type === 'Invoice'
          ? ['invoice_number', 'invoice_date', 'total_amount', 'subtotal', 'tax_amount', 'currency']
          : ['description', 'quantity', 'unit', 'unit_price', 'net_amount', 'vat_amount', 'gross_amount'];
        return factFields
          .filter(field => fields[field] !== null && fields[field] !== undefined)
          .map(field => ({ id: `${node.id}:${field}`, subject_name: node.label,
            predicate: field.replaceAll('_', ' '), value: String(fields[field]),
            status: ['approved', 'corrected'].includes(node.support?.[0]?.review_status) ? 'Verified' : 'Needs Review',
            source_doc_id: node.support?.[0]?.document_id, source_doc_name: fields.filename || 'Invoice' }));
      });
      return this.facts;
    } catch (error) { throw error; }
  }

  async getEvidence() {
    this.evidence = await this.request('/evidence').then(response => response.json());
    return this.evidence;
  }

  async getKnowledgeGraph(forceRefresh = false) {
    if (!forceRefresh && this.graphCache && Date.now() - this.graphCacheAt < 60000) return this.graphCache;
    if (this.graphPromise) return this.graphPromise;
    const request = (async () => {
      try {
      const res = await this.request('/graph');
      const graph = await res.json();
      const nodes = (graph.nodes || []).map(node => {
        const props = node.props || {};
        const label = String(node.type === 'Invoice' ? props.invoice_number || 'Invoice number not read'
          : node.type === 'SyntheticEmail' ? `Email ${String(props.thread_position || '').padStart(2, '0')} · ${props.invoice_number || ''}`
          : node.type === 'ConversationSummary' ? props.title || 'Synthetic conversation summary'
          : props.name || props.description || props.filename || node.id);
        return { id: node.id, type: node.type, label, name: label, data: props,
          support: node.support || [], color: node.type === 'Invoice' ? '#f59e0b'
            : node.type === 'SyntheticEmail' ? '#0f9f9a'
            : node.type === 'ConversationSummary' ? '#c24178' : undefined };
      });
      const edges = (graph.edges || []).map(edge => ({ id: edge.id, source: edge.source,
        target: edge.target, label: edge.type, type: edge.type, data: edge.props || {}, support: edge.support || [] }));
      this.graphCache = { nodes, edges, meta: { source: 'Paper Trail SQLite', node_count: nodes.length, edge_count: edges.length } };
      this.graphCacheAt = Date.now();
      return this.graphCache;
      } catch (error) { throw error; }
    })();
    this.graphPromise = request;
    try {
      return await request;
    } finally {
      if (this.graphPromise === request) this.graphPromise = null;
    }

  }

    async getConversationThreads() {
    if (this.conversationsCache && Date.now() - this.conversationsCacheAt < 10000) {
      return this.conversationsCache;
    }
    if (this.conversationsPromise) return this.conversationsPromise;
    const request = this.request('/conversations').then(response => response.json())
      .then(threads => {
        this.conversationsCache = Array.isArray(threads) ? threads : [];
        this.conversationsCacheAt = Date.now();
        return this.conversationsCache;
      }).catch(error => { throw error; });
    this.conversationsPromise = request;
    try {
      return await request;
    } finally {
      if (this.conversationsPromise === request) this.conversationsPromise = null;
    }
  }

  async getReviewItems() {
    try {
      const documents = await this.getDocuments();
      this.reviewItems = documents.filter(doc => doc.review_status === 'needs_review' && doc.extraction_state).map(doc => ({
        id: doc.id, status: 'Pending', category: doc.validation_status === 'ambiguous_review'
          ? 'Ambiguous Relationship' : 'Low Confidence', title: doc.filename,
        description: doc.summary, source_doc_id: doc.id, source_doc_name: doc.filename
      }));
      return this.reviewItems;
    } catch (error) { throw error; }
  }

  async approveReviewItem(itemId) {
    try {
      const detail = await (await this.request(`/documents/${encodeURIComponent(itemId)}`)).json();
      const extractionId = detail.extraction?.extraction_id;
      const res = await this.request(`/documents/${encodeURIComponent(itemId)}/review`, {
        method: 'POST', body: JSON.stringify({ decision: 'approved', expected_extraction_id: extractionId })
      });
      return await res.json();
    } catch (error) { return { success: false, error: error.message }; }
  }

  async rejectReviewItem(itemId) {
    return { success: false, error: 'Rejecting an extraction is not supported. Correct or approve the current extraction instead.' };
  }

  async getContradictions() {
    return this.request('/contradictions').then(response => response.json());
  }

  async getSemanticModel() {
    return this.request('/semantic-model').then(response => response.json());
  }

  async getAnalytics({ refresh = false } = {}) {
    if (!refresh && this.analyticsCache && Date.now() - this.analyticsCacheAt < 10000) {
      return this.analyticsCache;
    }
    if (this.analyticsPromise) return this.analyticsPromise;
    const request = this.request('/analytics').then(response => response.json());
    this.analyticsPromise = request;
    let serverAnalytics;
    try {
      serverAnalytics = await request;
    } finally {
      if (this.analyticsPromise === request) this.analyticsPromise = null;
    }
    this.analyticsCache = serverAnalytics;
    this.analyticsCacheAt = Date.now();
    return serverAnalytics;
  }

  async getSettings() {
    return this.request('/settings').then(response => response.json());
  }

  async correctDocument(itemId, correctedRecord) {
    const detail = await (await this.request(`/documents/${encodeURIComponent(itemId)}`)).json();
    const response = await this.request(`/documents/${encodeURIComponent(itemId)}/review`, {
      method: 'POST', body: JSON.stringify({ decision: 'corrected',
        expected_extraction_id: detail.extraction?.extraction_id,
        corrected_record: correctedRecord })
    });
    this.graphCache = null;
    this.documentsCache = null;
    this.analyticsCache = null;
    return response.json();
  }

  async search(query) {
    if (!query.trim()) return { documents: [], entities: [], relationships: [], facts: [] };
    return this.request(`/search?q=${encodeURIComponent(query)}`).then(response => response.json());
  }

}

export const aiService = new AIService();
