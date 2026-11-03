import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { 
  Search, Maximize2, Minimize2, Download, RefreshCw, 
  X, ExternalLink, Database, Layers, Building2, Box, 
  FileText, DollarSign, Sparkles, Plus, Minus, Target, 
  Share2, ShieldCheck, Copy, Check
} from 'lucide-react';
import { aiService } from '../services/aiService';

// Vibrant Neo4j Color Palette matching Bloom & Browser on Light Theme
const COLOR_MAP = {
  Invoice: '#f59e0b',      // Warm Amber/Gold
  SyntheticEmail: '#0f9f9a',
  ConversationSummary: '#c24178',
  Company: '#2563eb',      // Royal Blue
  Organization: '#2563eb', // Royal Blue
  Vendor: '#2563eb',
  Customer: '#7c3aed',
  Product: '#10b981',      // Emerald Green
  Money: '#06b6d4',        // Cyan / Teal
  Location: '#8b5cf6',     // Purple
  Date: '#64748b',         // Slate Grey
  Entity: '#f43f5e'        // Coral Red
};

export default function KnowledgeGraphPage() {
  const [activeTab, setActiveTab] = useState('graph'); // 'graph' | 'table' | 'raw'
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], meta: {} });
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNode, setSelectedNode] = useState(null);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [filterType, setFilterType] = useState('All');
  const [copied, setCopied] = useState(false);

  const containerRef = useRef(null);
  const sigmaContainerRef = useRef(null);
  const sigmaRef = useRef(null);
  const sigmaGraphRef = useRef(null);
  const viewStateRef = useRef({ searchQuery: '', filterType: 'All', selectedId: null, hoveredId: null });

  // Full Page Toggle with guaranteed CSS fallback
  const toggleFullscreen = useCallback(() => {
    if (!containerRef.current) return;

    if (!isFullscreen) {
      if (containerRef.current.requestFullscreen) {
        containerRef.current.requestFullscreen().catch(() => {});
      }
      setIsFullscreen(true);
    } else {
      if (document.fullscreenElement && document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      }
      setIsFullscreen(false);
    }
  }, [isFullscreen]);

  useEffect(() => {
    const handleFsChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFsChange);
    return () => document.removeEventListener('fullscreenchange', handleFsChange);
  }, []);

  // Fetch Graph Data from Neo4j
  const fetchGraph = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await aiService.getKnowledgeGraph();
      setGraphData(data);
    } catch (err) {
      console.error("Failed to load Neo4j graph:", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  useEffect(() => {
    if (activeTab !== 'graph' || !sigmaContainerRef.current || !graphData.nodes?.length) return undefined;

    let disposed = false;
    let rendererInstance = null;
    Promise.all([import('graphology'), import('sigma')]).then(([graphologyModule, sigmaModule]) => {
    if (disposed || !sigmaContainerRef.current) return;
    const Graph = graphologyModule.default;
    const Sigma = sigmaModule.default;
    const graph = new Graph({ type: 'undirected', multi: true, allowSelfLoops: true });
    const sourceNodes = new Map(graphData.nodes.map(node => [String(node.id), node]));
    const nodeDegrees = new Map();
    const adjacency = new Map();

    graphData.edges.forEach(edge => {
      const source = String(edge.source);
      const target = String(edge.target);
      nodeDegrees.set(source, (nodeDegrees.get(source) || 0) + 1);
      nodeDegrees.set(target, (nodeDegrees.get(target) || 0) + 1);
      if (!adjacency.has(source)) adjacency.set(source, []);
      if (!adjacency.has(target)) adjacency.set(target, []);
      adjacency.get(source).push(target);
      adjacency.get(target).push(source);
    });

    const hubIds = new Set(graphData.nodes.filter(node => {
      const type = node.type || 'Entity';
      const id = String(node.id);
      const invoiceThreadSummary = type === 'ConversationSummary'
        && node.data?.summary_scope === 'invoice_thread';
      return type === 'Invoice' || type === 'Company' || type === 'Organization'
        || (!invoiceThreadSummary && (nodeDegrees.get(id) || 0) >= 4);
    }).map(node => String(node.id)));
    const hubCount = hubIds.size;
    const hubColumns = Math.max(1, Math.ceil(Math.sqrt(hubCount * 1.6)));
    const positions = new Map();
    let hubIndex = 0;
    graphData.nodes.forEach((node, index) => {
      const id = String(node.id);
      if (!hubIds.has(id)) return;
      const column = hubIndex % hubColumns;
      const row = Math.floor(hubIndex / hubColumns);
      positions.set(id, { x: column * 340, y: row * 300 });
      hubIndex += 1;
    });
    const invoicePositions = new Map(graphData.nodes
      .filter(node => node.type === 'Invoice' && node.data?.invoice_number)
      .map(node => [String(node.data.invoice_number), positions.get(String(node.id))]));
    graphData.nodes.forEach((node, index) => {
      const id = String(node.id);
      if (positions.has(id)) return;
      const threadAnchor = invoicePositions.get(String(node.data?.invoice_number || ''));
      if (threadAnchor && node.type === 'ConversationSummary'
          && node.data?.summary_scope === 'invoice_thread') {
        positions.set(id, { x: threadAnchor.x, y: threadAnchor.y - 125 });
        return;
      }
      if (threadAnchor && node.type === 'SyntheticEmail') {
        const position = Math.max(1, Number(node.data?.thread_position) || 1) - 1;
        const column = position % 5;
        const row = Math.floor(position / 5);
        positions.set(id, { x: threadAnchor.x + (column - 2) * 58,
          y: threadAnchor.y + 125 + row * 48 });
        return;
      }
      const parentId = (adjacency.get(id) || []).find(neighbor => hubIds.has(neighbor));
      const parent = parentId ? positions.get(parentId) : null;
      const angle = index * 2.399963229728653;
      const distance = 70 + (index % 6) * 13;
      positions.set(id, parent
        ? { x: parent.x + Math.cos(angle) * distance, y: parent.y + Math.sin(angle) * distance }
        : { x: Math.cos(angle) * (80 + Math.sqrt(index) * 35), y: Math.sin(angle) * (80 + Math.sqrt(index) * 35) });
    });

    graphData.nodes.forEach(node => {
      const id = String(node.id);
      const original = sourceNodes.get(id);
      const type = node.type || 'Entity';
      const label = String(node.label || node.data?.invoice_number || node.id);
      graph.addNode(id, {
        label,
        x: positions.get(id)?.x ?? Math.cos(graph.order * 2.4) * 100,
        y: positions.get(id)?.y ?? Math.sin(graph.order * 2.4) * 100,
        size: type === 'Invoice' ? 24
          : type === 'ConversationSummary' ? 25
          : type === 'Company' || type === 'Organization' || type === 'Vendor' || type === 'Customer' ? 20
          : type === 'SyntheticEmail' ? 17 : 16,
        labelSize: type === 'Invoice' || type === 'ConversationSummary' ? 14 : 12,
        color: COLOR_MAP[type] || node.color || COLOR_MAP.Entity,
        type,
        degree: nodeDegrees.get(id) || 0,
        sourceDoc: original?.data?.filename || original?.data?.source_file
          || (type === 'SyntheticEmail' || type === 'ConversationSummary' ? 'Synthetic demo' : 'Invoice'),
        properties: original?.data || {},
        documentId: original?.support?.[0]?.document_id || null
      });
    });

    graphData.edges.forEach((edge, index) => {
      const source = String(edge.source);
      const target = String(edge.target);
      if (!graph.hasNode(source) || !graph.hasNode(target)) return;
      const key = `${String(edge.id || 'edge')}:${index}`;
      graph.addEdgeWithKey(key, source, target, {
        label: edge.label || edge.type || 'RELATED_TO',
        color: '#cbd5e1',
        size: 1
      });
    });

    sigmaGraphRef.current = graph;
    const renderer = new Sigma(graph, sigmaContainerRef.current, {
      renderEdgeLabels: false,
      labelDensity: 0.55,
      labelGridCellSize: 76,
      labelRenderedSizeThreshold: 4,
      enableNodeDrag: true,
      hideEdgesOnMove: true,
      hideLabelsOnMove: true,
      allowInvalidContainer: false,
      nodeReducer: (nodeId, displayData, attributes) => {
        const state = viewStateRef.current;
        const label = String(attributes.label || '').toLowerCase();
        const query = state.searchQuery.trim().toLowerCase();
        const matchesSearch = !query || label.includes(query) || String(attributes.type).toLowerCase().includes(query);
        const matchesType = state.filterType === 'All' || attributes.type.toLowerCase() === state.filterType.toLowerCase();
        const active = nodeId === state.selectedId || nodeId === state.hoveredId;
        const isSearchMatch = Boolean(query && matchesSearch && matchesType);
        return {
          ...displayData,
          color: matchesSearch && matchesType ? attributes.color : '#dbe2ea',
          size: active ? displayData.size * 1.35 : isSearchMatch ? displayData.size * 1.2 : displayData.size,
          labelSize: attributes.labelSize,
          labelVisibility: active || isSearchMatch ? 'visible' : displayData.labelVisibility,
          backdropVisibility: active || isSearchMatch ? 'visible' : 'hidden',
          backdropColor: '#ffffff',
          backdropBorderColor: attributes.color,
          backdropBorderWidth: 1.5,
          backdropPadding: 4,
          backdropCornerRadius: 5
        };
      },
      edgeReducer: (edgeId, displayData) => {
        const state = viewStateRef.current;
        const [source, target] = graph.extremities(edgeId);
        const active = source === state.selectedId || target === state.selectedId
          || source === state.hoveredId || target === state.hoveredId;
        return {
          ...displayData,
          color: active ? '#2563eb' : '#d5dce5',
          size: active ? 2.5 : 0.8,
        };
      }
    });
    sigmaRef.current = renderer;
    rendererInstance = renderer;

    const selectNode = (nodeId) => {
      const attributes = graph.getNodeAttributes(nodeId);
      setSelectedNode({ id: nodeId, ...attributes });
    };
    const enterNode = ({ node }) => {
      const attributes = graph.getNodeAttributes(node);
      setHoveredNode({ id: node, ...attributes });
    };
    renderer.on('clickNode', ({ node }) => selectNode(node));
    renderer.on('enterNode', enterNode);
    renderer.on('leaveNode', () => setHoveredNode(null));
    renderer.on('clickStage', () => setSelectedNode(null));
    }).catch(error => console.error('Could not initialize Sigma graph renderer:', error));

    return () => {
      disposed = true;
      rendererInstance?.kill();
      sigmaRef.current = null;
      sigmaGraphRef.current = null;
    };
  }, [graphData, activeTab]);

  useEffect(() => {
    viewStateRef.current = {
      searchQuery,
      filterType,
      selectedId: selectedNode?.id ? String(selectedNode.id) : null,
      hoveredId: hoveredNode?.id ? String(hoveredNode.id) : null
    };
    sigmaRef.current?.scheduleRefresh({ skipIndexation: true });

  }, [searchQuery, filterType, selectedNode, hoveredNode]);

  useEffect(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query || !sigmaGraphRef.current || !sigmaRef.current) return;
    const match = sigmaGraphRef.current.findNodes(node => {
      const data = sigmaGraphRef.current.getNodeAttributes(node);
      return String(data.label).toLowerCase().includes(query);
    })[0];
    if (match) {
      const { x, y } = sigmaGraphRef.current.getNodeAttributes(match);
      sigmaRef.current.getCamera().animate({ x, y, ratio: 0.25 }, { duration: 350 });
    }
  }, [searchQuery]);

  const selectGraphNode = useCallback((node) => {
    if (!node) return;
    const degree = (graphData.edges || []).filter(edge => String(edge.source) === String(node.id)
      || String(edge.target) === String(node.id)).length;
    setSelectedNode({ ...node, properties: node.properties || node.data || {},
      sourceDoc: node.sourceDoc || node.data?.filename || node.data?.source_file
        || (node.data?.synthetic ? 'Synthetic demo' : 'Invoice'), degree: node.degree ?? degree });
  }, [graphData.edges]);

  const resetCamera = () => {
    sigmaRef.current?.getCamera().reset({ duration: 350 });
  };

  const reSimulate = () => {
    resetCamera();
  };

  const exportPngSnapshot = () => {
    const renderer = sigmaRef.current;
    if (!renderer) return;
    const canvases = renderer.getCanvases();
    const { width, height } = renderer.getDimensions();
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) return;
    context.fillStyle = '#f8fafc';
    context.fillRect(0, 0, width, height);
    ['edges', 'nodes', 'edgeLabels', 'labels', 'hovers', 'hoverNodes'].forEach(layer => {
      if (canvases[layer]) context.drawImage(canvases[layer], 0, 0, width, height);
    });
    const link = document.createElement('a');
    link.download = `Paper_Trail_Knowledge_Graph_${Date.now()}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
  };

  const copyCypher = () => {
    const text = graphData.meta?.cypher || 'MATCH (a)-[r]->(b) RETURN a, r, b LIMIT 300';
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const nodeStats = useMemo(() => {
    const counts = {};
    (graphData.nodes || []).forEach(n => {
      const t = n.type || 'Entity';
      counts[t] = (counts[t] || 0) + 1;
    });
    return counts;
  }, [graphData.nodes]);

  // Connected relationships for selected node
  const selectedNodeRels = useMemo(() => {
    if (!selectedNode) return [];
    return (graphData.edges || []).filter(e => String(e.source) === String(selectedNode.id) || String(e.target) === String(selectedNode.id));
  }, [selectedNode, graphData.edges]);

  const selectedThreadData = useMemo(() => {
    if (!selectedNode) return { groups: [] };
    const edges = graphData.edges || [];
    const nodes = graphData.nodes || [];
    const invoiceIds = new Set();
    if (selectedNode.type === 'Invoice') invoiceIds.add(String(selectedNode.id));
    if (selectedNode.type === 'SyntheticEmail') {
      edges.filter(edge => edge.type === 'EMAIL_REFERENCES_INVOICE'
        && String(edge.source) === String(selectedNode.id))
        .forEach(edge => invoiceIds.add(String(edge.target)));
    }
    if (selectedNode.type === 'ConversationSummary') {
      edges.filter(edge => edge.type === 'SUMMARY_REFERENCES_INVOICE'
        && String(edge.source) === String(selectedNode.id))
        .forEach(edge => invoiceIds.add(String(edge.target)));
    }

    const groups = [...invoiceIds].map(invoiceId => {
      const invoice = nodes.find(node => String(node.id) === invoiceId);
      if (!invoice) return null;
      const emails = nodes.filter(node => node.type === 'SyntheticEmail'
        && edges.some(edge => edge.type === 'EMAIL_REFERENCES_INVOICE'
          && String(edge.source) === String(node.id) && String(edge.target) === invoiceId))
        .sort((left, right) => Number(left.data?.thread_position || 0)
          - Number(right.data?.thread_position || 0));
      const summaries = nodes.filter(node => node.type === 'ConversationSummary'
        && edges.some(edge => edge.type === 'SUMMARY_REFERENCES_INVOICE'
          && String(edge.source) === String(node.id) && String(edge.target) === invoiceId));
      const summary = summaries.find(node => node.data?.summary_scope === 'invoice_thread')
        || summaries[0];
      const vendorEdge = edges.find(edge => edge.type === 'ISSUED'
        && String(edge.target) === invoiceId);
      const customerEdge = edges.find(edge => edge.type === 'BILLED_TO'
        && String(edge.source) === invoiceId);
      const vendor = vendorEdge && nodes.find(node => String(node.id) === String(vendorEdge.source));
      const customer = customerEdge && nodes.find(node => String(node.id) === String(customerEdge.target));
      return { invoice, emails, summary, vendor, customer };
    }).filter(Boolean).sort((left, right) => String(left.invoice.data?.invoice_number || '')
      .localeCompare(String(right.invoice.data?.invoice_number || '')));
    return { groups };
  }, [selectedNode, graphData.nodes, graphData.edges]);

  const selectedPropertyEntries = useMemo(() => {
    const props = selectedNode?.properties || {};
    const longTextKeys = new Set(['summary_text', 'chunk_text', 'body', 'synthetic_notice']);
    return Object.entries(props).filter(([key, value]) => !longTextKeys.has(key)
      && value !== null && value !== undefined && value !== '')
      .map(([key, value]) => ({ key, label: key.replaceAll('_', ' '),
        value: Array.isArray(value) ? value.join(', ')
          : typeof value === 'object' ? JSON.stringify(value) : String(value) }));
  }, [selectedNode]);

  return (
    <div 
      ref={containerRef}
      style={{
        position: isFullscreen ? 'fixed' : 'relative',
        inset: isFullscreen ? 0 : 'auto',
        zIndex: isFullscreen ? 99999 : 1,
        width: isFullscreen ? '100vw' : '100%',
        height: isFullscreen ? '100vh' : 'calc(100vh - 110px)',
        backgroundColor: '#ffffff',
        borderRadius: isFullscreen ? 0 : 'var(--radius-lg)',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        boxShadow: isFullscreen ? 'none' : 'var(--shadow-md)',
        border: isFullscreen ? 'none' : '1px solid var(--border-light)',
        fontFamily: 'var(--font-sans)'
      }}
    >
      {/* ================= WEBSITE INTEGRATED TOP NAVIGATION BAR ================= */}
      <div style={{
        height: '52px',
        backgroundColor: '#ffffff',
        borderBottom: '1px solid var(--border-light)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 18px',
        userSelect: 'none'
      }}>
        {/* Left: View Tabs with Website Brand Theme */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', height: '100%' }}>
          <button
            onClick={() => setActiveTab('graph')}
            style={{
              height: '100%',
              padding: '0 16px',
              backgroundColor: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'graph' ? '3px solid var(--primary-600)' : '3px solid transparent',
              color: activeTab === 'graph' ? 'var(--primary-600)' : 'var(--text-secondary)',
              fontWeight: 700,
              fontSize: '0.875rem',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'all 0.15s ease'
            }}
          >
            Graph View
          </button>

          <button
            onClick={() => setActiveTab('table')}
            style={{
              height: '100%',
              padding: '0 16px',
              backgroundColor: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'table' ? '3px solid var(--primary-600)' : '3px solid transparent',
              color: activeTab === 'table' ? 'var(--primary-600)' : 'var(--text-secondary)',
              fontWeight: 700,
              fontSize: '0.875rem',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'all 0.15s ease'
            }}
          >
            Table View
          </button>

          <button
            onClick={() => setActiveTab('raw')}
            style={{
              height: '100%',
              padding: '0 16px',
              backgroundColor: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'raw' ? '3px solid var(--primary-600)' : '3px solid transparent',
              color: activeTab === 'raw' ? 'var(--primary-600)' : 'var(--text-secondary)',
              fontWeight: 700,
              fontSize: '0.875rem',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'all 0.15s ease'
            }}
          >
            Raw Cypher
          </button>
        </div>

        {/* Right Tools: Search, Filter, Fullscreen, Export */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* Light Theme Search Box */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            backgroundColor: 'var(--bg-app)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            padding: '5px 12px',
            gap: '8px',
            width: '220px'
          }}>
            <Search size={14} color="var(--text-muted)" />
            <input 
              type="text"
              placeholder="Search graph entities..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              style={{
                background: 'transparent',
                border: 'none',
                outline: 'none',
                color: 'var(--text-primary)',
                fontSize: '0.8rem',
                width: '100%'
              }}
            />
            {searchQuery && (
              <X size={12} color="var(--text-muted)" style={{ cursor: 'pointer' }} onClick={() => setSearchQuery('')} />
            )}
          </div>

          {/* Type Filter Select */}
          <select
            value={filterType}
            onChange={e => setFilterType(e.target.value)}
            style={{
              backgroundColor: '#ffffff',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-secondary)',
              fontSize: '0.8rem',
              fontWeight: 600,
              padding: '6px 10px',
              outline: 'none',
              cursor: 'pointer'
            }}
          >
            <option value="All">All Entities ({graphData.nodes?.length || 0})</option>
            <option value="Invoice">Invoices ({nodeStats.Invoice || 0})</option>
            <option value="Company">Companies ({nodeStats.Company || 0})</option>
            <option value="Vendor">Vendors ({nodeStats.Vendor || 0})</option>
            <option value="Customer">Customers ({nodeStats.Customer || 0})</option>
            <option value="Product">Products ({nodeStats.Product || 0})</option>
            <option value="Location">Locations ({nodeStats.Location || 0})</option>
            <option value="Date">Dates ({nodeStats.Date || 0})</option>
            <option value="SyntheticEmail">Synthetic Emails ({nodeStats.SyntheticEmail || 0})</option>
            <option value="ConversationSummary">Conversation Summary ({nodeStats.ConversationSummary || 0})</option>
          </select>

          {/* Re-simulate / Align */}
          <button
            onClick={reSimulate}
            title="Re-align Graph"
            className="btn btn-secondary btn-sm"
            style={{
              padding: '6px 8px',
              display: 'flex',
              alignItems: 'center',
              backgroundColor: '#ffffff',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer'
            }}
          >
            <RefreshCw size={14} color="var(--text-secondary)" />
          </button>

          {/* Export PNG */}
          <button
            onClick={exportPngSnapshot}
            title="Download Graph Snapshot"
            className="btn btn-secondary btn-sm"
            style={{
              padding: '6px 8px',
              display: 'flex',
              alignItems: 'center',
              backgroundColor: '#ffffff',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer'
            }}
          >
            <Download size={14} color="var(--text-secondary)" />
          </button>

          {/* FULL PAGE BUTTON */}
          <button
            onClick={toggleFullscreen}
            title={isFullscreen ? "Exit Full Page" : "Open Full Page"}
            className="btn btn-primary btn-sm"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '0.8rem',
              fontWeight: 700,
              padding: '6px 12px',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer',
              backgroundColor: isFullscreen ? 'var(--text-primary)' : 'var(--primary-600)',
              color: '#ffffff',
              border: 'none',
              transition: 'all 0.15s ease'
            }}
          >
            {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            <span>{isFullscreen ? "Exit Full" : "Full Page"}</span>
          </button>
        </div>
      </div>

      {/* ================= TAB 1: GRAPH CANVAS (CLEAN, NO OVERLAPPING BOXES) ================= */}
      {activeTab === 'graph' && (
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          
          <div
            ref={sigmaContainerRef}
            aria-label="Interactive invoice knowledge graph"
            style={{ position: 'absolute', inset: 0, background: '#f8fafc' }}
          />

          {/* Canvas Floating Zoom & Center Controls */}
          <div style={{
            position: 'absolute',
            bottom: '20px',
            left: '20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            zIndex: 10
          }}>
            <button
              onClick={() => sigmaRef.current?.getCamera().zoomIn({ duration: 200 })}
              title="Zoom In"
              style={{
                backgroundColor: '#ffffff',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
                width: '34px',
                height: '34px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                boxShadow: 'var(--shadow-sm)'
              }}
            >
              <Plus size={16} />
            </button>
            <button
              onClick={() => sigmaRef.current?.getCamera().zoomOut({ duration: 200 })}
              title="Zoom Out"
              style={{
                backgroundColor: '#ffffff',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
                width: '34px',
                height: '34px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                boxShadow: 'var(--shadow-sm)'
              }}
            >
              <Minus size={16} />
            </button>
            <button
              onClick={resetCamera}
              title="Fit Entire Graph in View"
              style={{
                backgroundColor: '#ffffff',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--primary-600)',
                width: '34px',
                height: '34px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                boxShadow: 'var(--shadow-sm)'
              }}
            >
              <Target size={16} />
            </button>
          </div>

          {/* Website Integrated Color Legend */}
          <div style={{
            position: 'absolute',
            bottom: '20px',
            right: selectedNode ? '340px' : '20px',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            backdropFilter: 'blur(8px)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            padding: '8px 14px',
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '14px',
            maxWidth: selectedNode ? 'min(760px, calc(100% - 360px))' : 'min(860px, calc(100% - 32px))',
            zIndex: 10,
            transition: 'right 0.2s ease',
            boxShadow: 'var(--shadow-md)'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: 'var(--text-primary)', fontWeight: 700 }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.Invoice, boxShadow: `0 0 4px ${COLOR_MAP.Invoice}` }} />
              <span>Invoice ({nodeStats.Invoice || 0})</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: 'var(--text-primary)', fontWeight: 700 }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.Company, boxShadow: `0 0 4px ${COLOR_MAP.Company}` }} />
              <span>Company ({nodeStats.Company || 0})</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: 'var(--text-primary)', fontWeight: 700 }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.Vendor }} />
              <span>Vendor ({nodeStats.Vendor || 0})</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: 'var(--text-primary)', fontWeight: 700 }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.Customer }} />
              <span>Customer ({nodeStats.Customer || 0})</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: 'var(--text-primary)', fontWeight: 700 }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.Product, boxShadow: `0 0 4px ${COLOR_MAP.Product}` }} />
              <span>Product ({nodeStats.Product || 0})</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: 'var(--text-primary)', fontWeight: 700 }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.Location, boxShadow: `0 0 4px ${COLOR_MAP.Location}` }} />
              <span>Location ({nodeStats.Location || 0})</span>
            </div>
            {nodeStats.SyntheticEmail > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: '#0f766e', fontWeight: 700 }}>
                <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.SyntheticEmail }} />
                <span>Synthetic email ({nodeStats.SyntheticEmail})</span>
              </div>
            )}
            {nodeStats.ConversationSummary > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: '#9d174d', fontWeight: 700 }}>
                <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: COLOR_MAP.ConversationSummary }} />
                <span>Agent summary</span>
              </div>
            )}
          </div>

          {/* Right Inspector Drawer for Selected Node (Website Light Theme) */}
          {selectedNode && (
            <div style={{
              position: 'absolute',
              top: '14px',
              right: '14px',
              bottom: '14px',
              width: 'min(440px, calc(100vw - 40px))',
              backgroundColor: '#ffffff',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-lg)',
              padding: '18px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              zIndex: 20,
              boxShadow: 'var(--shadow-xl)',
              overflowY: 'auto'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border-light)', paddingBottom: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: selectedNode.color || 'var(--primary-600)' }} />
                  <span style={{ fontSize: '0.8rem', fontWeight: 800, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    {selectedNode.type} Details
                  </span>
                </div>
                <button
                  onClick={() => setSelectedNode(null)}
                  style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '2px' }}
                >
                  <X size={16} />
                </button>
              </div>

              <div>
                <h3 style={{ fontSize: '1.15rem', fontWeight: 800, color: 'var(--text-primary)', margin: 0 }}>
                  {selectedNode.label}
                </h3>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  Graph node: {selectedNode.id}
                </span>
              </div>

              {/* Node Properties Card */}
              <div style={{ backgroundColor: 'var(--bg-app)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', padding: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <span style={{ fontSize: '0.7rem', fontWeight: 800, color: 'var(--primary-600)', textTransform: 'uppercase' }}>
                  Node details
                </span>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', display: 'flex', justifyContent: 'space-between' }}>
                  <span>Source Document:</span>
                  <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{selectedNode.sourceDoc || 'Invoice'}</span>
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', display: 'flex', justifyContent: 'space-between' }}>
                  <span>Graph Degree:</span>
                  <span style={{ color: 'var(--primary-600)', fontWeight: 700 }}>{selectedNode.degree || 1} connections</span>
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', display: 'flex', justifyContent: 'space-between' }}>
                  <span>Record status:</span>
                  <span style={{ color: selectedNode.properties?.synthetic ? '#a21caf' : 'var(--text-primary)', fontWeight: 700 }}>
                    {selectedNode.properties?.synthetic ? 'Synthetic demo · fictional' : selectedNode.properties?.review_status || 'Source graph'}
                  </span>
                </div>
              </div>

              {selectedPropertyEntries.length > 0 && (
                <div style={{ backgroundColor: '#ffffff', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', padding: '12px', display: 'grid', gridTemplateColumns: 'minmax(90px, 0.7fr) minmax(0, 1.3fr)', gap: '8px 12px' }}>
                  {selectedPropertyEntries.map(({ key, label, value }) => (
                    <React.Fragment key={key}>
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem', textTransform: 'capitalize' }}>{label}</span>
                      <span style={{ color: 'var(--text-primary)', fontSize: '0.75rem', fontWeight: 600, overflowWrap: 'anywhere' }}>{value}</span>
                    </React.Fragment>
                  ))}
                </div>
              )}

              {(selectedNode.properties?.summary_text || selectedNode.properties?.body) && (
                <div style={{ backgroundColor: selectedNode.properties?.synthetic ? '#fdf4ff' : '#f8fafc', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', padding: '12px', maxHeight: '240px', overflowY: 'auto' }}>
                  <div style={{ fontSize: '0.7rem', fontWeight: 800, color: selectedNode.properties?.synthetic ? '#a21caf' : 'var(--primary-600)', textTransform: 'uppercase', marginBottom: '7px' }}>
                    {selectedNode.type === 'ConversationSummary' ? 'Conversation summary' : selectedNode.type === 'SyntheticEmail' ? 'Email summary' : 'Node context'}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>
                    {selectedNode.properties.summary_text || selectedNode.properties.body}
                  </div>
                  {selectedNode.type === 'SyntheticEmail' && selectedNode.properties.body && (
                    <details style={{ marginTop: '10px', fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
                      <summary style={{ cursor: 'pointer', fontWeight: 700 }}>Show full email body</summary>
                      <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>{selectedNode.properties.body}</p>
                    </details>
                  )}
                </div>
              )}

              {selectedThreadData.groups.length > 0 && (
                <section style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: '0.75rem', fontWeight: 800, color: 'var(--text-secondary)', textTransform: 'uppercase' }}>
                      Conversation timeline
                    </span>
                    <span style={{ fontSize: '0.68rem', color: '#a21caf', fontWeight: 700 }}>Synthetic demo · fictional
                    </span>
                  </div>
                  {selectedThreadData.groups.map(group => {
                    const invoiceNumber = group.invoice.data?.invoice_number || group.invoice.label;
                    return (
                      <details key={group.invoice.id} open={selectedThreadData.groups.length === 1}
                        style={{ border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', background: '#fff', overflow: 'hidden' }}>
                        <summary style={{ cursor: 'pointer', padding: '10px 12px', background: '#f8fafc', fontSize: '0.78rem', fontWeight: 800, color: 'var(--text-primary)' }}>
                          Invoice {invoiceNumber} · {group.emails.length} messages
                        </summary>
                        <div style={{ padding: '10px', display: 'flex', flexDirection: 'column', gap: '9px' }}>
                          <div style={{ padding: '10px', borderRadius: '8px', background: '#fdf4ff', border: '1px solid #f5d0fe' }}>
                            <div style={{ fontSize: '0.72rem', fontWeight: 800, color: '#86198f', marginBottom: '5px' }}>
                              {group.vendor?.label || group.summary?.data?.vendor_name || 'Vendor'} ↔ {group.customer?.label || group.summary?.data?.customer_name || 'Customer'}
                            </div>
                            <div style={{ fontSize: '0.73rem', lineHeight: 1.5, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
                              {group.summary?.data?.summary_text || 'No conversation summary is attached to this invoice.'}
                            </div>
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
                            {group.emails.map(email => (
                              <article key={email.id} style={{ display: 'grid', gridTemplateColumns: '28px minmax(0, 1fr)', gap: '8px', alignItems: 'stretch' }}>
                                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                                  <span style={{ width: '24px', height: '24px', display: 'grid', placeItems: 'center', borderRadius: '50%', background: '#ccfbf1', color: '#0f766e', fontSize: '0.65rem', fontWeight: 800 }}>
                                    {String(email.data?.thread_position || '').padStart(2, '0')}
                                  </span>
                                  <span style={{ flex: 1, minHeight: '12px', borderLeft: '1px solid #99f6e4' }} />
                                </div>
                                <div style={{ border: '1px solid var(--border-light)', borderRadius: '8px', padding: '9px', background: '#fff' }}>
                                  <div style={{ fontSize: '0.74rem', fontWeight: 800, color: 'var(--text-primary)' }}>{email.data?.subject || email.label}</div>
                                  <div style={{ marginTop: '3px', fontSize: '0.67rem', color: 'var(--text-muted)' }}>
                                    {email.data?.sender_name || 'Sender'} → {email.data?.recipient_name || 'Recipient'} · {email.data?.sent_at || 'Date unavailable'}
                                  </div>
                                  <div style={{ marginTop: '6px', fontSize: '0.72rem', lineHeight: 1.45, color: 'var(--text-secondary)' }}>
                                    {email.data?.summary_text || email.data?.body || 'No message text available.'}
                                  </div>
                                  {email.data?.body && (
                                    <details style={{ marginTop: '6px', fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                                      <summary style={{ cursor: 'pointer' }}>Full message</summary>
                                      <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.4 }}>{email.data.body}</p>
                                    </details>
                                  )}
                                </div>
                              </article>
                            ))}
                          </div>
                        </div>
                      </details>
                    );
                  })}
                </section>
              )}

              {/* Connected Relationships */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <span style={{ fontSize: '0.75rem', fontWeight: 800, color: 'var(--text-secondary)', textTransform: 'uppercase' }}>
                  Connected Relationships ({selectedNodeRels.length})
                </span>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '220px', overflowY: 'auto' }}>
                  {selectedNodeRels.map((rel, idx) => {
                    const isOutgoing = String(rel.source) === String(selectedNode.id);
                    const relatedId = isOutgoing ? rel.target : rel.source;
                    const relatedNode = graphData.nodes.find(node => String(node.id) === String(relatedId));
                    return (
                      <div key={idx} style={{
                        padding: '8px 10px',
                        backgroundColor: '#ffffff',
                        border: '1px solid var(--border-light)',
                        borderRadius: 'var(--radius-sm)',
                        fontSize: '0.75rem',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '8px',
                        cursor: relatedNode ? 'pointer' : 'default'
                      }}>
                        <span onClick={() => selectGraphNode(relatedNode)} style={{ color: isOutgoing ? 'var(--primary-600)' : '#db2777', fontWeight: 700, fontSize: '0.7rem' }}>
                          {isOutgoing ? 'OUT →' : '← IN'} {rel.label}
                        </span>
                        <span onClick={() => selectGraphNode(relatedNode)} style={{ color: 'var(--text-secondary)', fontSize: '0.7rem', fontWeight: 600, textAlign: 'right' }}>
                          {relatedNode?.label || relatedId}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ================= TAB 2: TABLE VIEW (LIGHT THEME) ================= */}
      {activeTab === 'table' && (
        <div style={{ flex: 1, backgroundColor: 'var(--bg-app)', padding: '20px', overflowY: 'auto' }}>
          <div style={{ marginBottom: '14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: '0.9rem', fontWeight: 700, color: 'var(--text-primary)' }}>
              All Active Knowledge Graph Records ({graphData.nodes?.length || 0} nodes, {graphData.edges?.length || 0} relationships)
            </span>
            <span style={{ fontSize: '0.8rem', color: 'var(--primary-600)', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
              Cypher: {"MATCH (a)-[r]->(b) RETURN a, r, b"}
            </span>
          </div>

          <div style={{ border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', overflow: 'hidden', backgroundColor: '#ffffff', boxShadow: 'var(--shadow-xs)' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', color: 'var(--text-primary)' }}>
              <thead>
                <tr style={{ backgroundColor: 'var(--bg-app)', borderBottom: '1px solid var(--border-light)', textAlign: 'left' }}>
                  <th style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontWeight: 700 }}>#</th>
                  <th style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontWeight: 700 }}>Node ID</th>
                  <th style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontWeight: 700 }}>Label</th>
                  <th style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontWeight: 700 }}>Name / Value</th>
                  <th style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontWeight: 700 }}>Status</th>
                  <th style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontWeight: 700 }}>Summary / source</th>
                </tr>
              </thead>
              <tbody>
                {(graphData.nodes || []).map((n, idx) => (
                  <tr 
                    key={n.id} 
                    onClick={() => { selectGraphNode(n); setActiveTab('graph'); }}
                    style={{ 
                      borderBottom: '1px solid var(--border-light)', 
                      backgroundColor: idx % 2 === 0 ? '#ffffff' : '#fafafa',
                      cursor: 'pointer'
                    }}
                  >
                    <td style={{ padding: '10px 16px', color: 'var(--text-muted)' }}>{idx + 1}</td>
                    <td style={{ padding: '10px 16px', fontFamily: 'var(--font-mono)', color: 'var(--primary-600)', fontSize: '0.75rem' }}>{n.id}</td>
                    <td style={{ padding: '10px 16px' }}>
                      <span style={{
                        padding: '3px 9px',
                        borderRadius: '999px',
                        backgroundColor: `${COLOR_MAP[n.type] || '#8b5cf6'}18`,
                        color: COLOR_MAP[n.type] || '#8b5cf6',
                        fontWeight: 700,
                        fontSize: '0.725rem'
                      }}>
                        {n.type}
                      </span>
                    </td>
                    <td style={{ padding: '10px 16px', fontWeight: 700, color: 'var(--text-primary)' }}>{n.label}</td>
                    <td style={{ padding: '10px 16px', color: n.data?.synthetic ? '#a21caf' : 'var(--text-secondary)', fontWeight: 700, fontSize: '0.78rem' }}>
                      {n.data?.synthetic ? 'Synthetic · fictional' : n.data?.review_status || '—'}
                    </td>
                    <td style={{ padding: '10px 16px', color: 'var(--text-muted)', fontSize: '0.78rem', maxWidth: '420px' }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {String(n.data?.summary_text || n.data?.body || n.data?.filename || n.data?.source_file || '—').slice(0, 180)}
                      </div>
                      {n.data?.filename || n.data?.source_file ? <small>{n.data.filename || n.data.source_file}</small> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ================= TAB 3: RAW CYPHER & JSON VIEW (LIGHT THEME) ================= */}
      {activeTab === 'raw' && (
        <div style={{ flex: 1, backgroundColor: 'var(--bg-app)', padding: '20px', overflowY: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '0.9rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                Raw Graph API Response Payload
              </span>
              <span className="badge badge-blue">
                JSON
              </span>
            </div>
            <button
              onClick={copyCypher}
              className="btn btn-secondary btn-sm"
              style={{
                backgroundColor: '#ffffff',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
                padding: '6px 14px',
                fontSize: '0.8rem',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px'
              }}
            >
              {copied ? <Check size={14} color="var(--success-600)" /> : <Copy size={14} />}
              <span>{copied ? "Copied!" : "Copy Cypher & JSON"}</span>
            </button>
          </div>

          <div style={{
            backgroundColor: '#ffffff',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            padding: '18px',
            fontFamily: 'var(--font-mono)',
            fontSize: '0.8rem',
            color: 'var(--text-primary)',
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            boxShadow: 'var(--shadow-xs)'
          }}>
            {`// Active Cypher Query executed on 4550f285.databases.neo4j.io
MATCH (a)-[r]->(b)
RETURN a, r, b
LIMIT 300;

// Summary Metadata:
// Total Active Nodes: ${graphData.nodes?.length || 0}
// Total Active Relationships: ${graphData.edges?.length || 0}
// Storage Engine: Neo4j AuraDB Free Tier

${JSON.stringify(graphData, null, 2)}`}
          </div>
        </div>
      )}
    </div>
  );
}
