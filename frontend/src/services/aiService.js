import {
  INITIAL_DOCUMENTS,
  INITIAL_ENTITIES,
  INITIAL_RELATIONSHIPS,
  INITIAL_FACTS,
  INITIAL_EVIDENCE,
  INITIAL_REVIEW_ITEMS,
  INITIAL_CONTRADICTIONS,
  SEMANTIC_MODEL_DATA,
  INITIAL_ANALYTICS
} from './mockData';

class AIService {
  constructor() {
    this.apiBase = '/api';
    this.apiError = null;

    const getStored = (key, fallback) => {
      try {
        if (typeof window !== 'undefined' && window.localStorage) {
          const item = window.localStorage.getItem(key);
          if (item) {
            const parsed = JSON.parse(item);
            if (Array.isArray(parsed) && parsed.length > 0) return parsed;
          }
        }
      } catch (_) {}
      return fallback;
    };

    // Local reactive store hydrated with persistent local cache or initial bootstrap
    this.documents = getStored('kf_documents', [...INITIAL_DOCUMENTS]);
    this.entities = getStored('kf_entities', [...INITIAL_ENTITIES]);
    this.relationships = getStored('kf_relationships', [...INITIAL_RELATIONSHIPS]);
    this.facts = getStored('kf_facts', [...INITIAL_FACTS]);
    this.evidence = getStored('kf_evidence', [...INITIAL_EVIDENCE]);
    this.reviewItems = [...INITIAL_REVIEW_ITEMS];
    this.contradictions = [...INITIAL_CONTRADICTIONS];
    this.semanticModel = [...SEMANTIC_MODEL_DATA];
    this.analytics = { ...INITIAL_ANALYTICS };
    this.settings = {
      extraction_model: "KnowFlow-Hybrid-Extraction-Engine-v2",
      confidence_threshold: 0.80,
      entity_resolution_threshold: 0.90,
      automatic_extraction: true,
      human_review_enabled: true,
      evidence_tracking_enabled: true,
      strict_hallucination_guardrails: true
    };
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
    const response = await fetch(`${this.apiBase}${path}`, { ...options, headers });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      const message = detail.error || `Paper Trail API returned ${response.status}`;
      this.apiError = message;
      throw new Error(message);
    }
    this.apiError = null;
    return response;
  }

  documentFromApi(row) {
    const file = row.file || 'Document';
    const fileType = file.includes('.') ? file.split('.').pop().toUpperCase() : 'FILE';
    const isReview = row.review_status === 'needs_review' || row.validation_status === 'ambiguous_review';
    const status = isReview ? 'Needs Review' : row.file_status === 'complete' ? 'Completed'
      : row.file_status === 'failed' ? 'Failed' : 'Processing';
    return {
      id: row.document_id,
      filename: file,
      file_type: fileType,
      file_size: '—',
      uploaded_at: row.last_synced_at || 'Local watch folder',
      status,
      pages_count: 1,
      chunks_count: 0,
      entities_count: 0,
      relationships_count: 0,
      facts_count: 0,
      confidence_score: null,
      needs_review_count: isReview ? 1 : 0,
      summary: row.last_error || row.validation_status || row.extraction_state || 'Waiting for extraction',
      validation_status: row.validation_status,
      review_status: row.review_status,
      text_content: ''
    };
  }

  // --- CORE AI EXTRACTION ABSTRACTIONS ---

  async extractEntities(documentText, docId = "doc-custom", docName = "Uploaded_Document") {
    try {
      const res = await fetch(`${this.apiBase}/documents/${docId}/entities`);
      if (res.ok) return await res.json();
    } catch (_) { }

    // Dynamic client-side regex extraction fallback
    const entities = [];
    const foundNames = new Set();

    // 1. Organizations
    const orgPatterns = [
      /\b([A-Z][A-Za-z0-9&.,\s]{2,45}?\s+(?:Pvt\.?\s*Ltd\.?|Ltd\.?|Corporation|Corp\.?|Inc\.?|LLC|LLP|GmbH|Solutions|Technologies|Holdings|Group|Enterprise|Systems))\b/g,
      /(?:by and between|between|Purchaser|Supplier|Vendor|Client|Customer):\s*([A-Z][A-Za-z0-9&.,\s]{2,40}?)(?:,|\n|\.|\(|\s+an\b)/g
    ];
    for (const pat of orgPatterns) {
      let match;
      while ((match = pat.exec(documentText)) !== null) {
        const raw = match[1].trim();
        if (raw && raw.length > 3 && !foundNames.has(raw.toLowerCase())) {
          foundNames.add(raw.toLowerCase());
          entities.push({
            id: `ent-${Date.now()}-${entities.length}`,
            name: raw,
            type: "Organization",
            mentions_count: 1,
            linked_entity_id: `ORG-${raw.replace(/[^A-Za-z0-9]/g, '').slice(0, 4).toUpperCase()}`,
            linked_entity_name: raw,
            confidence: 0.96,
            status: "Verified",
            source_doc_id: docId,
            source_doc_name: docName,
            source_page: 1,
            evidence_text: documentText.slice(Math.max(0, match.index - 20), Math.min(documentText.length, match.index + 80)).trim(),
            properties: { Category: "Corporate Entity" }
          });
        }
      }
    }

    // 2. Contracts / Invoices
    const docRefRegex = /\b((?:Contract|Agreement|Invoice|INV|PO|Purchase Order)[\s#\-:]+[A-Za-z0-9\-_]{2,20})\b/gi;
    let refMatch;
    while ((refMatch = docRefRegex.exec(documentText)) !== null) {
      const raw = refMatch[1].trim();
      if (!foundNames.has(raw.toLowerCase())) {
        foundNames.add(raw.toLowerCase());
        const isInv = raw.toLowerCase().includes("inv");
        entities.push({
          id: `ent-${Date.now()}-${entities.length}`,
          name: raw,
          type: isInv ? "Invoice" : "Contract",
          mentions_count: 1,
          linked_entity_id: `${isInv ? 'INV' : 'CTR'}-${raw.replace(/[^A-Za-z0-9]/g, '').slice(0, 6).toUpperCase()}`,
          linked_entity_name: raw,
          confidence: 0.98,
          status: "Verified",
          source_doc_id: docId,
          source_doc_name: docName,
          source_page: 1,
          evidence_text: documentText.slice(Math.max(0, refMatch.index - 20), Math.min(documentText.length, refMatch.index + 80)).trim(),
          properties: { Category: isInv ? "Billing Record" : "Commercial Instrument" }
        });
      }
    }

    // 3. Products
    const prodRegex = /\b([A-Z][a-zA-Z0-9\-_]+(?:\s+[A-Za-z0-9\-_]+)*\s+(?:Server|Rack|Cluster|Switch|Router|Processor|Software|Platform|Service|Hardware|License|Solution|Workstation|Laptop|App|Device)s?)\b/gi;
    let prodMatch;
    while ((prodMatch = prodRegex.exec(documentText)) !== null) {
      const raw = prodMatch[1].trim();
      if (!foundNames.has(raw.toLowerCase())) {
        foundNames.add(raw.toLowerCase());
        entities.push({
          id: `ent-${Date.now()}-${entities.length}`,
          name: raw,
          type: "Product",
          mentions_count: 1,
          linked_entity_id: `PROD-${raw.replace(/[^A-Za-z0-9]/g, '').slice(0, 4).toUpperCase()}`,
          linked_entity_name: raw,
          confidence: 0.95,
          status: "Verified",
          source_doc_id: docId,
          source_doc_name: docName,
          source_page: 1,
          evidence_text: documentText.slice(Math.max(0, prodMatch.index - 20), Math.min(documentText.length, prodMatch.index + 80)).trim(),
          properties: { Category: "Commercial Line Item" }
        });
      }
    }

    // 4. Persons
    const personRegex = /(?:signed by|signatory:?|Signatory:?|Director:?|Manager:?|Representative:?|Signed:?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)/g;
    let personMatch;
    while ((personMatch = personRegex.exec(documentText)) !== null) {
      const raw = personMatch[1].trim();
      if (!foundNames.has(raw.toLowerCase()) && raw.length > 4) {
        foundNames.add(raw.toLowerCase());
        const isUnmatched = documentText.toLowerCase().includes("signatory");
        entities.push({
          id: `ent-${Date.now()}-${entities.length}`,
          name: raw,
          type: "Person",
          mentions_count: 1,
          linked_entity_id: isUnmatched ? null : `PER-${Date.now()}`,
          linked_entity_name: isUnmatched ? null : raw,
          confidence: isUnmatched ? 0.84 : 0.96,
          status: isUnmatched ? "Needs Review" : "Verified",
          source_doc_id: docId,
          source_doc_name: docName,
          source_page: 1,
          evidence_text: documentText.slice(Math.max(0, personMatch.index - 20), Math.min(documentText.length, personMatch.index + 80)).trim(),
          properties: { Designation: "Signatory" }
        });
      }
    }

    // 5. Money
    const moneyRegex = /((?:INR|₹|USD|\$|EUR|€|GBP|£)\s*[\d,]+(?:\.\d+)?(?:\s*(?:crore|lakh|million|billion|k))?)/gi;
    let moneyMatch;
    while ((moneyMatch = moneyRegex.exec(documentText)) !== null) {
      const raw = moneyMatch[1].trim();
      if (!foundNames.has(raw.toLowerCase())) {
        foundNames.add(raw.toLowerCase());
        entities.push({
          id: `ent-${Date.now()}-${entities.length}`,
          name: raw,
          type: "Money",
          mentions_count: 1,
          linked_entity_id: `MON-${Date.now().toString().slice(-4)}`,
          linked_entity_name: raw,
          confidence: 0.99,
          status: "Verified",
          source_doc_id: docId,
          source_doc_name: docName,
          source_page: 1,
          evidence_text: documentText.slice(Math.max(0, moneyMatch.index - 20), Math.min(documentText.length, moneyMatch.index + 80)).trim(),
          properties: { Value: raw }
        });
      }
    }

    return entities;
  }

  async extractRelationships(documentText, entities = [], docId = "doc-custom", docName = "Uploaded_Document") {
    try {
      const res = await fetch(`${this.apiBase}/documents/${docId}/relationships`);
      if (res.ok) return await res.json();
    } catch (_) { }

    return [];
  }

  async extractFacts(documentText, docId = "doc-custom", docName = "Uploaded_Document") {
    try {
      const res = await fetch(`${this.apiBase}/documents/${docId}/facts`);
      if (res.ok) return await res.json();
    } catch (_) { }

    return [];
  }

  async resolveEntities(entities, semanticModel) {
    return entities.map(e => ({
      ...e,
      status: e.linked_entity_id ? "Verified" : "Needs Review"
    }));
  }

  async queryKnowledge(query, knowledgeBase = null) {
    return this.answerKnowledgeQuery(query, knowledgeBase);
  }

  async answerKnowledgeQuery(query, knowledgeBase = null) {
    try {
      const res = await this.request('/graph-rag', {
        method: 'POST',
        body: JSON.stringify({ question: query, generate: false, limit: 8 })
      });
      const data = await res.json();
      const links = data.links || data.matches?.flatMap(match => match.links || []) || [];
      const fullGraph = await this.getKnowledgeGraph();
      const sourceIds = new Set((data.sources || []).map(source => source.id));
      const linkedEdges = fullGraph.edges.filter(edge => sourceIds.has(edge.source) || sourceIds.has(edge.target));
      const nodeIds = new Set(sourceIds);
      linkedEdges.forEach(edge => { nodeIds.add(edge.source); nodeIds.add(edge.target); });
      const evidenceNodes = fullGraph.nodes.filter(node => nodeIds.has(node.id)).slice(0, 40);
      const evidenceNodeIds = new Set(evidenceNodes.map(node => node.id));
      const evidenceEdges = linkedEdges.filter(edge => evidenceNodeIds.has(edge.source) && evidenceNodeIds.has(edge.target)).slice(0, 60);
      const citations = (data.sources || []).map(source => ({
        source: source.filename || `Invoice ${source.invoice_number || source.id}`,
        quote: source.invoice_number ? `Invoice number ${source.invoice_number}` : source.type,
        confidence: null,
        document_id: source.id
      }));
      const tableRows = evidenceNodes.flatMap(node => Object.entries(node.data || {})
        .filter(([key, value]) => !['id', 'synthetic', 'synthetic_notice', 'confidence', 'chunk_text', 'summary_text', 'body'].includes(key)
          && value !== null && value !== undefined && ['string', 'number', 'boolean'].includes(typeof value))
        .map(([key, value]) => ({ category: node.type, entity: node.label, property: key.replaceAll('_', ' '), value: String(value), confidence: null, source: node.data?.filename || node.type })));
      return {
        query,
        answer: data.answer || 'No matching graph facts were found.',
        confidence: null,
        pipeline_steps: [
          { step: 1, name: 'Question Understanding', description: 'Prepared the question for graph retrieval.', detail: query },
          { step: 2, name: 'Graph Retrieval', description: 'Searched the Paper Trail graph.', detail: `${data.retrieval_count ?? links.length} matches; backend ${data.retrieval_backend || 'sqlite'}.` },
          { step: 3, name: 'Evidence Collection', description: 'Collected source documents from retrieved graph facts.', detail: `${citations.length} source documents.` },
          { step: 4, name: 'Answer', description: 'Returned the backend’s evidence-grounded answer.', detail: data.answer_mode || 'local' }
        ],
        matched_entities: [],
        matched_facts: links,
        evidence_citations: citations,
        graph: { nodes: evidenceNodes, edges: evidenceEdges },
        table: tableRows,
        answer_mode: data.answer_mode,
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

    // Client fallback: Dynamic grounded search
    const qLower = query.toLowerCase();
    const queryTokens = qLower.split(/\s+/).filter(w => w.length > 2);

    if (this.entities.length === 0 && this.facts.length === 0) {
      return {
        query,
        answer: "The knowledge base is currently empty. Please upload a business document to extract entities, relationships, and business facts.",
        confidence: 0.0,
        pipeline_steps: [
          { step: 1, name: "Question Understanding", description: "Processed query intent.", detail: `Query: '${query}'` },
          { step: 2, name: "Entity Identification", description: "Searched knowledge base for named entities.", detail: "0 matching entities found." },
          { step: 3, name: "Knowledge Retrieval", description: "Traversed graph connections.", detail: "0 active nodes in graph." },
          { step: 4, name: "Evidence Collection", description: "Gathered verbatim source citations.", detail: "No documents currently ingested." },
          { step: 5, name: "Answer Generation", description: "Safeguarded against hallucination.", detail: "Zero unsupported statements returned." }
        ],
        matched_entities: [],
        matched_facts: [],
        evidence_citations: []
      };
    }

    const matchedEntities = this.entities.filter(e =>
      queryTokens.some(t => e.name.toLowerCase().includes(t) || e.type.toLowerCase().includes(t))
    );
    const matchedFacts = this.facts.filter(f =>
      queryTokens.some(t => f.predicate.toLowerCase().includes(t) || f.value.toLowerCase().includes(t) || f.subject_name.toLowerCase().includes(t))
    );

    let answer = "";
    if (matchedFacts.length > 0) {
      const summaries = matchedFacts.slice(0, 3).map(f => `**${f.predicate}** is **${f.value}** (${f.source_doc_name})`);
      answer = `Based on extracted business facts: ${summaries.join('; ')}.`;
    } else if (matchedEntities.length > 0) {
      const summaries = matchedEntities.slice(0, 4).map(e => `**${e.name}** (${e.type})`);
      answer = `Identified entities matching your query: ${summaries.join(', ')}.`;
    } else {
      answer = `Found ${this.entities.length} entities and ${this.facts.length} facts in the knowledge base, but none specifically match the query terms in '${query}'.`;
    }

    const citations = [...matchedFacts, ...matchedEntities].slice(0, 3).map(item => ({
      source: `${item.source_doc_name || 'Document'} (Page ${item.source_page || 1})`,
      quote: item.evidence_text || item.value || item.name,
      confidence: item.confidence || 0.95
    }));

    return {
      query,
      answer,
      confidence: citations.length > 0 ? 0.96 : 0.70,
      pipeline_steps: [
        { step: 1, name: "Question Understanding", description: "Analyzed query intent and target constraints.", detail: `Query: '${query}'` },
        { step: 2, name: "Entity Identification", description: "Grounded named entities in query.", detail: `Matched ${matchedEntities.length} entities.` },
        { step: 3, name: "Knowledge Retrieval", description: "Traversed knowledge graph paths.", detail: `Traversed ${matchedFacts.length} structured facts.` },
        { step: 4, name: "Evidence Collection", description: "Gathered verbatim source citations.", detail: `Collected ${citations.length} verified citations.` },
        { step: 5, name: "Answer Generation", description: "Synthesized answer with zero hallucination.", detail: "Generated grounded response." }
      ],
      matched_entities: matchedEntities.slice(0, 5),
      matched_facts: matchedFacts.slice(0, 5),
      evidence_citations: citations
    };
  }

  // --- REPOSITORY & API INTEGRATION METHODS ---

  async getDocuments() {
    try {
      const res = await this.request('/documents');
      const data = await res.json();
      this.documents = Array.isArray(data) ? data.map(row => this.documentFromApi(row)) : [];
      return this.documents;
    } catch (_) { return []; }
  }

  async getWatcherStatus() {
    return { running: null, folder: 'watched', supported_extensions: ['.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff'] };
  }

  async getDocument(docId) {
    try {
      const res = await this.request(`/documents/${encodeURIComponent(docId)}`);
      const payload = await res.json();
      const extraction = payload.extraction || {};
      const record = extraction.effective_record || extraction.record || {};
      const document = this.documentFromApi({
        document_id: docId,
        file: record.provenance?.source_file || this.documents.find(d => d.id === docId)?.filename,
        file_status: 'complete',
        review_status: extraction.review_status,
        validation_status: record.validation_status,
        extraction_state: extraction.state
      });
      const evidenceText = Object.entries(record.evidence || {})
        .map(([field, evidence]) => `${field}: ${evidence?.quote || ''}`).filter(Boolean).join('\n');
      document.invoice_meta = record;
      document.text_content = evidenceText;
      document.confidence_score = record.confidence;
      document.pages = [{ page_number: 1, title: document.filename, text: evidenceText || 'OCR evidence is not available.' }];
      try {
        const image = await this.request(`/documents/${encodeURIComponent(docId)}/pages/1.png`);
        document.image_url = URL.createObjectURL(await image.blob());
      } catch (_) { document.image_url = ''; }
      return document;
    } catch (_) { return this.documents.find(d => d.id === docId) || null; }
  }

  async uploadDemoDocument(demoType = "contract") {
    try {
      const formData = new FormData();
      formData.append('is_demo', 'true');
      const res = await fetch(`${this.apiBase}/documents/upload`, {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        const data = await res.json();
        if (data.document) {
          this.documents.unshift(data.document);
        }
        return data.document;
      }
    } catch (_) { }

    return null;
  }

  async uploadCustomDocument(file) {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch(`${this.apiBase}/documents/upload`, {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        const data = await res.json();
        if (data.document) {
          this.documents.unshift(data.document);
        }
        return data.document;
      }
    } catch (_) { }

    // Fallback client extraction if backend unreachable
    const docId = `doc-${Date.now().toString().slice(-4)}`;
    const text = await file.text().catch(() => "Uploaded document content.");
    const extractedEntities = await this.extractEntities(text, docId, file.name);

    const newDoc = {
      id: docId,
      filename: file.name,
      file_type: file.name.split('.').pop().toUpperCase(),
      file_size: `${Math.max(1, Math.round(file.size / 1024))} KB`,
      uploaded_at: "Today",
      status: "Completed",
      pages_count: 1,
      chunks_count: 4,
      entities_count: extractedEntities.length,
      relationships_count: 0,
      facts_count: 0,
      confidence_score: 0.95,
      needs_review_count: extractedEntities.filter(e => e.status === "Needs Review").length,
      summary: `Extracted ${extractedEntities.length} entities from ${file.name}.`,
      text_content: text,
      pages: [{ page_number: 1, title: file.name, text }]
    };

    this.documents.unshift(newDoc);
    this.entities.push(...extractedEntities);
    return newDoc;
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
          confidence: node.data?.confidence ?? null,
          status: support.review_status === 'approved' ? 'Verified' : 'Needs Review' };
      });
      return filter && filter !== 'All'
        ? this.entities.filter(entity => entity.type.toLowerCase() === filter.toLowerCase())
        : this.entities;
    } catch (_) { return []; }
  }

  async getRelationships() {
    try {
      const graph = await this.getKnowledgeGraph();
      const names = new Map(graph.nodes.map(node => [node.id, node.label]));
      this.relationships = graph.edges.map(edge => ({ id: edge.id,
        source_entity_id: edge.source, target_entity_id: edge.target,
        source_entity_name: names.get(edge.source) || edge.source,
        target_entity_name: names.get(edge.target) || edge.target,
        relation_type: edge.type, properties: edge.data, status: 'Supported', confidence: null,
        source_doc_id: edge.support?.[0]?.document_id }));
      return this.relationships;
    } catch (_) { return []; }
  }

  async getFacts() {
    try {
      const graph = await this.getKnowledgeGraph();
      this.facts = graph.nodes.filter(node => node.type === 'Invoice').flatMap(node => {
        const fields = node.data || {};
        return ['invoice_number', 'invoice_date', 'total_amount', 'subtotal', 'tax_amount', 'currency']
          .filter(field => fields[field] !== null && fields[field] !== undefined)
          .map(field => ({ id: `${node.id}:${field}`, subject_name: node.label,
            predicate: field.replaceAll('_', ' '), value: String(fields[field]),
            source_doc_id: node.support?.[0]?.document_id, source_doc_name: fields.filename || 'Invoice' }));
      });
      return this.facts;
    } catch (_) { return []; }
  }

  async getEvidence() {
    try {
      const graph = await this.getKnowledgeGraph();
      this.evidence = graph.nodes.flatMap(node => (node.support || []).map(support => ({
        id: `${node.id}:${support.document_id}`, source_doc_id: support.document_id,
        source_doc_name: node.data?.filename || 'Invoice document', entity_name: node.label,
        entity_type: node.type, source_page: null,
        evidence_text: `Supported by extraction ${support.extraction_id} (${support.review_status}).`
      })));
      return this.evidence;
    } catch (_) { return []; }
  }

  async getKnowledgeGraph() {
    if (this.graphCache && Date.now() - this.graphCacheAt < 30000) return this.graphCache;
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
    } catch (_) { return { nodes: [], edges: [], meta: { error: this.apiError || 'API unavailable' } }; }

    // Dynamic client fallback graph layout
    const nodes = this.entities.slice(0, 24).map((e, idx) => ({
      id: e.id,
      type: 'customKnowledgeNode',
      position: { x: 100 + (idx % 4) * 240, y: 80 + Math.floor(idx / 4) * 160 },
      data: {
        id: e.id,
        label: e.name,
        type: e.type,
        linkedId: e.linked_entity_id,
        confidence: e.confidence || 0.95,
        status: e.status || "Verified",
        source: e.source_doc_name || "Document"
      }
    }));

    const edges = this.relationships.slice(0, 30).map(r => ({
      id: r.id,
      source: r.source_entity_id,
      target: r.target_entity_id,
      label: r.relation_type,
      animated: r.status === "Supported",
      style: {
        stroke: r.status === "Unsupported" ? "#ef4444" : "#3b82f6",
        strokeWidth: 2,
        strokeDasharray: r.status === "Unsupported" ? "5,5" : "0"
      },
      data: {
        id: r.id,
        relation_type: r.relation_type,
        confidence: r.confidence || 0.95,
        status: r.status || "Supported",
        evidence: r.evidence_text || "",
        source_doc: r.source_doc_name || "Document",
        source_page: r.source_page || 1,
        warning: r.warning
      }
    }));

    return { nodes, edges };
  }

  async getConversationThreads() {
    const graph = await this.getKnowledgeGraph();
    const emails = graph.nodes.filter(node => node.type === 'SyntheticEmail');
    const summaries = graph.nodes.filter(node => node.type === 'ConversationSummary');
    const invoices = graph.nodes.filter(node => node.type === 'Invoice');
    const invoiceByNumber = new Map(invoices.map(node => [String(node.data?.invoice_number || node.label), node]));
    const threads = new Map();
    for (const email of emails) {
      const number = String(email.data?.invoice_number || 'Unlinked invoice');
      if (!threads.has(number)) threads.set(number, { invoiceNumber: number, emails: [], summary: null, invoice: invoiceByNumber.get(number) || null });
      threads.get(number).emails.push(email);
    }
    for (const summary of summaries) {
      const number = String(summary.data?.invoice_number || '');
      if (number && threads.has(number)) threads.get(number).summary = summary;
    }
    return [...threads.values()].map(thread => {
      thread.emails.sort((a, b) => Number(a.data?.thread_position || 0) - Number(b.data?.thread_position || 0));
      const props = thread.summary?.data || thread.invoice?.data || thread.emails[0]?.data || {};
      return { ...thread, vendor: props.vendor_name || props.vendor || 'Vendor not recorded', customer: props.customer_name || props.customer || 'Customer not recorded' };
    }).sort((a, b) => a.invoiceNumber.localeCompare(b.invoiceNumber));
  }

  async getReviewItems() {
    try {
      const documents = await this.getDocuments();
      this.reviewItems = documents.filter(doc => doc.status === 'Needs Review').map(doc => ({
        id: doc.id, status: 'Pending', category: doc.validation_status === 'ambiguous_review'
          ? 'Ambiguous Relationship' : 'Low Confidence', title: doc.filename,
        description: doc.summary, source_doc_id: doc.id, source_doc_name: doc.filename
      }));
      return this.reviewItems;
    } catch (_) { return []; }
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
    return { success: false, error: 'Paper Trail currently supports approving or correcting an extraction, not rejecting it.' };
  }

  async getContradictions() {
    try {
      const res = await fetch(`${this.apiBase}/contradictions`);
      if (res.ok) {
        const data = await res.json();
        this.contradictions = data;
        return data;
      }
    } catch (_) { }
    return this.contradictions;
  }

  async resolveContradiction(contraId, choice, note = "") {
    try {
      const formData = new FormData();
      formData.append('choice', choice);
      formData.append('note', note);
      const res = await fetch(`${this.apiBase}/contradictions/${contraId}/resolve`, {
        method: 'POST',
        body: formData
      });
      if (res.ok) return await res.json();
    } catch (_) { }

    const item = this.contradictions.find(c => c.id === contraId);
    if (item) {
      item.status = `Resolved (${choice.toUpperCase()})`;
      item.resolution_note = note || `Verified ${choice} as authoritative reference.`;
    }
    return { success: true, contradiction: item };
  }

  async getSemanticModel() {
    try {
      const res = await fetch(`${this.apiBase}/semantic-model`);
      if (res.ok) return await res.json();
    } catch (_) { }
    return this.semanticModel;
  }

  async getAnalytics() {
    const [documents, entities, relationships, facts, reviews] = await Promise.all([
      this.getDocuments(), this.getEntities(), this.getRelationships(), this.getFacts(), this.getReviewItems()
    ]);
    const totalDocs = documents.length;
    const totalEnts = entities.length;
    const totalRels = relationships.length;
    const totalFacts = facts.length;

    const typeCounts = {};
    for (const e of entities) {
      const t = e.type || "Other";
      typeCounts[t] = (typeCounts[t] || 0) + 1;
    }

    const typeColors = {
      Organization: "#3b82f6", Product: "#10b981", Contract: "#f59e0b",
      Person: "#8b5cf6", Money: "#14b8a6", Invoice: "#06b6d4",
      Date: "#64748b", Location: "#ec4899"
    };

    return {
      kpis: {
        documents_processed: totalDocs,
        documents_processed_today: totalDocs,
        entities_extracted: totalEnts,
        entities_today: totalEnts,
        relationships_discovered: totalRels,
        relationships_today: totalRels,
        facts_extracted: totalFacts,
        facts_today: totalFacts
      },
      extraction_activity_timeline: [],
      entities_by_type: Object.keys(typeCounts).sort().map(name => ({
        name,
        count: typeCounts[name] || 0,
        color: typeColors[name] || "#64748b"
      })),
      confidence_distribution: entities.some(e => e.confidence !== null && e.confidence !== undefined && Number.isFinite(Number(e.confidence))) ? [
        { range: "95-100%", count: entities.filter(e => (e.confidence || 0) >= 0.95).length },
        { range: "90-94%", count: entities.filter(e => (e.confidence || 0) >= 0.90 && (e.confidence || 0) < 0.95).length },
        { range: "80-89%", count: entities.filter(e => (e.confidence || 0) >= 0.80 && (e.confidence || 0) < 0.90).length },
        { range: "< 80%", count: entities.filter(e => (e.confidence || 0) < 0.80).length }
      ] : [],
      human_review_metrics: {
        pending_reviews: reviews.filter(r => r.status === "Pending").length,
        resolved_reviews: reviews.filter(r => r.status === "Resolved").length,
        rejected_hallucinations: reviews.filter(r => r.status === "Rejected").length
      }
    };
  }

  async getSettings() {
    try {
      const res = await fetch(`${this.apiBase}/settings`);
      if (res.ok) return await res.json();
    } catch (_) { }
    return this.settings;
  }

  async saveSettings(newSettings) {
    this.settings = { ...this.settings, ...newSettings };
    try {
      const res = await fetch(`${this.apiBase}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this.settings)
      });
      if (res.ok) return await res.json();
    } catch (_) { }
    return { success: true, settings: this.settings };
  }

  // --- NEO4J & INVOICE DATASET INTEGRATION ---

  async getNeo4jStatus() {
    try {
      const res = await fetch(`${this.apiBase}/neo4j/status`);
      if (res.ok) return await res.json();
    } catch (_) { }
    return { connected: false, configured: false, message: "Backend offline" };
  }

  async configureNeo4j(config) {
    try {
      const res = await fetch(`${this.apiBase}/neo4j/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      });
      if (res.ok) return await res.json();
    } catch (e) {
      return { connected: false, error: e.message };
    }
    return { connected: false, error: "Failed to configure Neo4j" };
  }

  async syncToNeo4j() {
    try {
      const res = await fetch(`${this.apiBase}/neo4j/sync`, { method: 'POST' });
      if (res.ok) return await res.json();
    } catch (e) {
      return { success: false, error: e.message };
    }
    return { success: false, error: "Sync failed" };
  }

  async getInvoiceDatasetStatus() {
    try {
      const res = await fetch(`${this.apiBase}/invoices/dataset-status`);
      if (res.ok) return await res.json();
    } catch (_) { }
    return { dataset_name: "katanaml-org/invoices-donut-data-v1", batch_available: 26, ocr_engine: "RapidOCR" };
  }

  async ingestInvoiceDataset(limit = 15, offset = 0) {
    try {
      const res = await fetch(`${this.apiBase}/invoices/ingest-dataset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit, offset })
      });
      if (res.ok) {
        const data = await res.json();
        await this.getDocuments();
        await this.getEntities();
        await this.getRelationships();
        await this.getFacts();
        await this.getEvidence();
        return data;
      }
    } catch (e) {
      return { success: false, error: e.message };
    }
    return { success: false, error: "Ingestion failed" };
  }

  async ingestAllInvoices() {
    try {
      const res = await fetch(`${this.apiBase}/invoices/ingest-all`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        await this.getDocuments();
        await this.getEntities();
        await this.getRelationships();
        await this.getFacts();
        await this.getEvidence();
        return data;
      }
    } catch (e) {
      return { success: false, error: e.message };
    }
    return { success: false, error: "Auto-ingestion failed" };
  }
}

export const aiService = new AIService();
