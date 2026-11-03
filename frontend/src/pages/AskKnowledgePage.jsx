import React, { useState, useRef, useEffect, useCallback } from 'react';
import { 
  Sparkles, Send, ShieldCheck, FileText, ChevronDown, 
  ChevronUp, ExternalLink, Network, Table, AlignLeft, CheckCircle2,
  ZoomIn, ZoomOut, RotateCcw, Maximize2, Minimize2, Info, Layers, X
} from 'lucide-react';
import ConfidenceBadge from '../components/common/ConfidenceBadge';
import EntityBadge from '../components/common/EntityBadge';
import { aiService } from '../services/aiService';

function renderAnswerInline(text, keyPrefix) {
  return String(text).split(/(\*\*[^*]+\*\*|\[(?:N|C|T|P|S)\d+\])/g).filter(Boolean).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={`${keyPrefix}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    if (/^\[(?:N|C|T|P|S)\d+\]$/.test(part)) {
      return <span key={`${keyPrefix}-${index}`} className="badge badge-blue"
        style={{ margin: '0 2px', fontSize: '0.68rem', verticalAlign: 'middle' }}>{part.slice(1, -1)}</span>;
    }
    return part;
  });
}

// --- EVIDENCE SUBGRAPH COMPONENT ---
function InteractiveSubgraph({ graphData }) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const rawNodes = graphData?.nodes || [];
  const rawEdges = graphData?.edges || [];
  const nodeById = new Map(rawNodes.map(node => [node.id, node]));
  const detailProperties = Object.entries(selectedNode?.data || selectedNode?.properties || {})
    .filter(([key, value]) => !['summary_text', 'chunk_text', 'body', 'synthetic_notice'].includes(key)
      && value !== null && value !== undefined && ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 7);
  const selectedRelationships = selectedNode ? rawEdges.filter(edge =>
    edge.source === selectedNode.id || edge.target === selectedNode.id).slice(0, 6) : [];

  // Internal simulation and camera refs
  const nodesRef = useRef([]);
  const edgesRef = useRef([]);
  const cameraRef = useRef({ x: 0, y: 0, zoom: 1 });
  const isDraggingRef = useRef(false);
  const draggedNodeRef = useRef(null);
  const lastMousePosRef = useRef({ x: 0, y: 0 });

  // Initialize node positions & edge bindings
  useEffect(() => {
    if (!rawNodes || rawNodes.length === 0) return;

    const width = 850;
    const height = 520;
    const cx = width / 2;
    const cy = height / 2;

    const typeColors = {
      Organization: '#2563eb',
      Company: '#2563eb',
      Customer: '#7c3aed',
      Vendor: '#0f766e',
      Supplier: '#0f766e',
      Product: '#10b981',
      LineItem: '#16a34a',
      Invoice: '#06b6d4',
      SyntheticEmail: '#0f9f9a',
      ConversationSummary: '#c24178',
      Document: '#64748b',
      Money: '#14b8a6',
      Date: '#64748b',
      Issue: '#ef4444'
    };

    // Keep invoice, customer, and vendor records in distinct columns so the
    // evidence path reads left to right instead of collapsing into a circle.
    const outDegree = {};
    rawEdges.forEach(e => {
      outDegree[e.source] = (outDegree[e.source] || 0) + 1;
    });
    const simNodes = rawNodes.map((n) => {
      const isParty = ['Organization', 'Company', 'Customer', 'Vendor', 'Supplier'].includes(n.type);
      const isInvoice = n.type === 'Invoice';
      const radius = isParty || isInvoice ? 35 : 29;
      const color = n.color || typeColors[n.type] || '#8b5cf6';

      return {
        ...n,
        color,
        radius,
        isHub: isParty || isInvoice || (outDegree[n.id] || 0) > 1,
        x: cx,
        y: cy,
        vx: 0,
        vy: 0
      };
    });

    const customers = simNodes.filter(n => ['Customer', 'Organization', 'Company'].includes(n.type));
    const invoices = simNodes.filter(n => n.type === 'Invoice');
    const vendors = simNodes.filter(n => ['Vendor', 'Supplier'].includes(n.type));
    const attached = simNodes.filter(n => !customers.includes(n) && !invoices.includes(n) && !vendors.includes(n));
    const placeColumn = (group, x) => group.forEach((node, index) => {
      node.x = x;
      node.y = 70 + ((height - 140) * (index + 1)) / (group.length + 1);
    });
    if (invoices.length) {
      placeColumn(vendors, 125);
      placeColumn(invoices, cx);
      placeColumn(customers, 725);
      placeColumn(attached, vendors.length || customers.length ? 560 : 725);
    } else {
      const degreeOrder = [...simNodes].sort((a, b) =>
        (outDegree[b.id] || 0) - (outDegree[a.id] || 0));
      const hubs = degreeOrder.filter(node => node.isHub);
      const leaves = degreeOrder.filter(node => !node.isHub);
      placeColumn(hubs, 280);
      placeColumn(leaves, 650);
      if (!hubs.length) placeColumn(degreeOrder, cx);
    }

    // Resolve edge endpoints by id or label
    const simEdges = [];
    rawEdges.forEach((e, idx) => {
      const srcNode = simNodes.find(n => n.id === e.source || n.label === e.source);
      const tgtNode = simNodes.find(n => n.id === e.target || n.label === e.target);
      if (srcNode && tgtNode) {
        simEdges.push({
          id: e.id || `edge-${idx}`,
          source: srcNode,
          target: tgtNode,
          label: e.label || 'RELATED_TO'
        });
      }
    });

    nodesRef.current = simNodes;
    edgesRef.current = simEdges;
    cameraRef.current = { x: cx, y: cy, zoom: 1 };
    setZoomLevel(1);
    setSelectedNode(null);

    render();
  }, [graphData]);

  // Canvas drawing routine
  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;

    // Website Light Theme Background: #f8fafc
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    // Camera Transform (Pan & Zoom)
    ctx.translate(width / 2, height / 2);
    ctx.scale(cameraRef.current.zoom, cameraRef.current.zoom);
    ctx.translate(-cameraRef.current.x, -cameraRef.current.y);

    // Subtle background grid dots
    ctx.fillStyle = '#e2e8f0';
    const gridSize = 40;
    const startX = Math.floor((cameraRef.current.x - width / (2 * cameraRef.current.zoom)) / gridSize) * gridSize;
    const endX = Math.ceil((cameraRef.current.x + width / (2 * cameraRef.current.zoom)) / gridSize) * gridSize;
    const startY = Math.floor((cameraRef.current.y - height / (2 * cameraRef.current.zoom)) / gridSize) * gridSize;
    const endY = Math.ceil((cameraRef.current.y + height / (2 * cameraRef.current.zoom)) / gridSize) * gridSize;

    for (let gx = startX; gx <= endX; gx += gridSize) {
      for (let gy = startY; gy <= endY; gy += gridSize) {
        ctx.fillRect(gx - 1, gy - 1, 2, 2);
      }
    }

    const nodes = nodesRef.current;
    const edges = edgesRef.current;

    // 1. Draw Relationship Lines with Directed Arrows & Relationship Name Badges
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const src = e.source;
      const tgt = e.target;

      const isConnected = selectedNode?.id === src.id || selectedNode?.id === tgt.id ||
                          hoveredNode?.id === src.id || hoveredNode?.id === tgt.id;

      // Vector calculations
      const dx = tgt.x - src.x;
      const dy = tgt.y - src.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / dist;
      const uy = dy / dist;

      // Start & End points outside the node circles
      const sx = src.x + ux * (src.radius + 2);
      const sy = src.y + uy * (src.radius + 2);
      const tx = tgt.x - ux * (tgt.radius + 8);
      const ty = tgt.y - uy * (tgt.radius + 8);

      // Draw connecting line
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(tx, ty);
      ctx.strokeStyle = isConnected ? '#2563eb' : '#94a3b8';
      ctx.lineWidth = isConnected ? 2.5 : 1.8;
      ctx.stroke();

      // Draw directed arrowhead pointing into target node
      const arrowLen = 10;
      const arrowWidth = 7;
      const ax1 = tx - ux * arrowLen + uy * arrowWidth;
      const ay1 = ty - uy * arrowLen - ux * arrowWidth;
      const ax2 = tx - ux * arrowLen - uy * arrowWidth;
      const ay2 = ty - uy * arrowLen + ux * arrowWidth;

      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(ax1, ay1);
      ctx.lineTo(ax2, ay2);
      ctx.closePath();
      ctx.fillStyle = isConnected ? '#2563eb' : '#64748b';
      ctx.fill();

      // Draw relationship label pill directly on the connecting line
      const mx = (sx + tx) / 2;
      const my = (sy + ty) / 2;

      ctx.font = '700 10px sans-serif';
      const labelText = e.label.toUpperCase();
      const textWidth = ctx.measureText(labelText).width;
      const padX = 8;
      const padY = 4;
      const badgeW = textWidth + padX * 2;
      const badgeH = 18;

      ctx.fillStyle = isConnected ? '#1e40af' : '#334155';
      ctx.beginPath();
      ctx.roundRect(mx - badgeW / 2, my - badgeH / 2, badgeW, badgeH, 4);
      ctx.fill();

      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(labelText, mx, my);
    }

    // 2. Draw graph nodes with a distinct visual treatment for cited evidence.
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const isSelected = selectedNode?.id === n.id;
      const isHovered = hoveredNode?.id === n.id;

      // Cited source nodes have a persistent teal ring; selection adds a blue halo.
      if (n.isEvidenceSource) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius + 3, 0, Math.PI * 2);
        ctx.strokeStyle = '#0f766e';
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Outer Highlight Ring
      if (isSelected || isHovered) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius + 6, 0, Math.PI * 2);
        ctx.fillStyle = isSelected ? 'rgba(37, 99, 235, 0.25)' : 'rgba(37, 99, 235, 0.15)';
        ctx.fill();
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Circular Node Body
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
      ctx.fillStyle = n.color || '#3b82f6';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Node Icon / Short Initial inside circle
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = '800 12px sans-serif';
      const shortInitial = (n.type || 'E').slice(0, 3).toUpperCase();
      ctx.fillText(shortInitial, n.x, n.y);

      // Node Name Label Box below circle
      ctx.font = '700 11px sans-serif';
      const fullLabel = n.label || n.id;
      const truncLabel = fullLabel.length > 24 ? fullLabel.slice(0, 22) + '...' : fullLabel;
      const tw = ctx.measureText(truncLabel).width;

      const lx = n.x;
      const ly = n.y + n.radius + 14;

      ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
      ctx.beginPath();
      ctx.roundRect(lx - tw / 2 - 6, ly - 9, tw + 12, 18, 4);
      ctx.fill();

      ctx.strokeStyle = isSelected ? '#2563eb' : '#cbd5e1';
      ctx.lineWidth = isSelected ? 1.5 : 1;
      ctx.stroke();

      ctx.fillStyle = '#0f172a';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(truncLabel, lx, ly);

      // Type Badge above circle
      const typeBadge = n.type ? n.type.toUpperCase() : 'NODE';
      ctx.font = '700 8.5px sans-serif';
      const tbw = ctx.measureText(typeBadge).width;
      const tyBadge = n.y - n.radius - 10;

      ctx.fillStyle = n.color || '#64748b';
      ctx.beginPath();
      ctx.roundRect(n.x - tbw / 2 - 4, tyBadge - 7, tbw + 8, 14, 3);
      ctx.fill();

      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(typeBadge, n.x, tyBadge);
    }

    ctx.restore();
  }, [selectedNode, hoveredNode]);

  // Coordinate conversion helper
  const getCanvasCoords = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) * (canvas.width / rect.width);
    const mouseY = (e.clientY - rect.top) * (canvas.height / rect.height);

    const width = canvas.width;
    const height = canvas.height;

    // Inverse camera transformation
    const worldX = (mouseX - width / 2) / cameraRef.current.zoom + cameraRef.current.x;
    const worldY = (mouseY - height / 2) / cameraRef.current.zoom + cameraRef.current.y;

    return { worldX, worldY, mouseX, mouseY };
  };

  // Mouse event handlers for smooth interaction
  const handleMouseDown = (e) => {
    const { worldX, worldY, mouseX, mouseY } = getCanvasCoords(e);
    lastMousePosRef.current = { x: mouseX, y: mouseY };

    // Check if clicked a node
    const nodes = nodesRef.current;
    let clicked = null;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = worldX - n.x;
      const dy = worldY - n.y;
      if (dx * dx + dy * dy <= (n.radius + 4) * (n.radius + 4)) {
        clicked = n;
        break;
      }
    }

    if (clicked) {
      draggedNodeRef.current = clicked;
      setSelectedNode(clicked);
    } else {
      isDraggingRef.current = true;
      setSelectedNode(null);
    }
    render();
  };

  const handleMouseMove = (e) => {
    const { worldX, worldY, mouseX, mouseY } = getCanvasCoords(e);

    if (draggedNodeRef.current) {
      draggedNodeRef.current.x = worldX;
      draggedNodeRef.current.y = worldY;
      render();
      return;
    }

    if (isDraggingRef.current) {
      const dx = (mouseX - lastMousePosRef.current.x) / cameraRef.current.zoom;
      const dy = (mouseY - lastMousePosRef.current.y) / cameraRef.current.zoom;
      cameraRef.current.x -= dx;
      cameraRef.current.y -= dy;
      lastMousePosRef.current = { x: mouseX, y: mouseY };
      render();
      return;
    }

    // Hover detection
    const nodes = nodesRef.current;
    let hovered = null;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = worldX - n.x;
      const dy = worldY - n.y;
      if (dx * dx + dy * dy <= (n.radius + 4) * (n.radius + 4)) {
        hovered = n;
        break;
      }
    }

    if (hovered !== hoveredNode) {
      setHoveredNode(hovered);
      render();
    }
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
    draggedNodeRef.current = null;
  };

  const handleWheel = (e) => {
    e.preventDefault();
    const zoomDelta = e.deltaY > 0 ? 0.9 : 1.1;
    const newZoom = Math.min(Math.max(cameraRef.current.zoom * zoomDelta, 0.4), 3.0);
    cameraRef.current.zoom = newZoom;
    setZoomLevel(newZoom);
    render();
  };

  const handleZoom = (factor) => {
    const newZoom = Math.min(Math.max(cameraRef.current.zoom * factor, 0.4), 3.0);
    cameraRef.current.zoom = newZoom;
    setZoomLevel(newZoom);
    render();
  };

  const handleReset = () => {
    cameraRef.current = { x: 425, y: 260, zoom: 1 };
    setZoomLevel(1);
    setSelectedNode(null);
    render();
  };

  // Re-render when selected node changes
  useEffect(() => {
    render();
  }, [render, selectedNode, hoveredNode]);

  if (!rawNodes || rawNodes.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
        No graph relationship nodes found for this query.
      </div>
    );
  }

  return (
    <div 
      ref={containerRef}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid #cbd5e1',
        overflow: 'hidden',
        boxShadow: '0 4px 14px rgba(0,0,0,0.06)',
        backgroundColor: '#f8fafc',
        ...(isFullscreen ? { position: 'fixed', inset: '12px', zIndex: 1000, height: 'calc(100vh - 24px)' } : {})
      }}
    >
      {/* Graph Toolbar Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 16px',
        backgroundColor: '#ffffff',
        borderBottom: '1px solid #e2e8f0',
        zIndex: 10
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Network size={16} color="#2563eb" />
          <span style={{ fontSize: '0.85rem', fontWeight: 800, color: '#0f172a' }}>
            Retrieved evidence subgraph
          </span>
          <span className="badge badge-blue" style={{ fontSize: '0.7rem' }}>
            {rawNodes.length} Nodes • {rawEdges.length} Relations
          </span>
        </div>

        {/* Legend & Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {/* Node Legend */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: '8px', fontSize: '0.725rem', fontWeight: 600 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#7c3aed' }} /> Customer</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#0f766e' }} /> Vendor</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#06b6d4' }} /> Invoice</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><span style={{ width: '12px', height: '8px', border: '1px dashed #0f766e', borderRadius: '50%' }} /> Cited</span>
          </div>

          <button 
            className="btn btn-outline btn-sm"
            onClick={() => handleZoom(1.2)}
            title="Zoom In"
            style={{ padding: '4px 8px' }}
          >
            <ZoomIn size={14} />
          </button>
          <button 
            className="btn btn-outline btn-sm"
            onClick={() => handleZoom(0.8)}
            title="Zoom Out"
            style={{ padding: '4px 8px' }}
          >
            <ZoomOut size={14} />
          </button>
          <button 
            className="btn btn-outline btn-sm"
            onClick={handleReset}
            title="Center View"
            style={{ padding: '4px 8px' }}
          >
            <RotateCcw size={14} />
          </button>
          <button className="btn btn-outline btn-sm" onClick={() => setIsFullscreen(value => !value)}
            title={isFullscreen ? 'Exit full screen' : 'Expand graph'} style={{ padding: '4px 8px' }}>
            {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      {/* Main Graph Canvas */}
      <div style={{ position: 'relative', width: '100%', height: isFullscreen ? 'calc(100vh - 84px)' : '520px', cursor: hoveredNode ? 'grab' : isDraggingRef.current ? 'grabbing' : 'default' }}>
        <canvas
          ref={canvasRef}
          width={850}
          height={520}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          style={{ display: 'block', width: '100%', height: '100%' }}
        />

        {/* Selected Node Details Floating Card */}
        {selectedNode && (
          <div style={{
            position: 'absolute',
            bottom: '14px',
            left: '14px',
            backgroundColor: '#ffffff',
            borderRadius: '8px',
            padding: '14px 18px',
            boxShadow: '0 10px 25px rgba(0,0,0,0.12)',
            border: '2px solid #2563eb',
            maxWidth: '320px',
            zIndex: 20,
            display: 'flex',
            flexDirection: 'column',
            gap: '6px'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span className="badge" style={{ backgroundColor: selectedNode.color || '#3b82f6', color: '#ffffff', fontSize: '0.65rem' }}>
                {selectedNode.type}
              </span>
              <button 
                onClick={() => setSelectedNode(null)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              >
                <X size={14} color="#64748b" />
              </button>
            </div>
            <div style={{ fontWeight: 800, fontSize: '0.9rem', color: '#0f172a' }}>
              {selectedNode.label}
            </div>
            {detailProperties.length > 0 && (
              <div style={{ fontSize: '0.75rem', color: '#475569', display: 'flex', flexDirection: 'column', gap: '2px', marginTop: '4px', borderTop: '1px solid #f1f5f9', paddingTop: '4px' }}>
                {detailProperties.map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ fontWeight: 600, color: '#64748b' }}>{k}:</span>
                    <span style={{ fontWeight: 700, color: '#1e3a8a', textAlign: 'right', maxWidth: '210px', overflowWrap: 'anywhere' }}>
                      {String(v).length > 110 ? `${String(v).slice(0, 107)}…` : String(v)}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {selectedRelationships.length > 0 && <div style={{ marginTop: '4px', borderTop: '1px solid #f1f5f9', paddingTop: '5px' }}>
              <div style={{ fontSize: '0.68rem', fontWeight: 800, color: '#475569', marginBottom: '3px' }}>CONNECTED RECORDS</div>
              {selectedRelationships.map(edge => {
                const otherId = edge.source === selectedNode.id ? edge.target : edge.source;
                const other = nodeById.get(otherId);
                const direction = edge.source === selectedNode.id ? '→' : '←';
                return <div key={edge.id} style={{ fontSize: '0.7rem', color: '#334155', marginTop: '3px' }}>
                  {direction} <b>{edge.label || edge.type}</b> {direction} {other?.label || otherId}
                </div>;
              })}
            </div>}
            <div style={{ fontSize: '0.675rem', color: '#059669', fontWeight: 600, marginTop: '2px' }}>
              {selectedNode.isEvidenceSource ? 'Cited as answer evidence' : 'Connected graph context'}
            </div>
          </div>
        )}

        {/* Bottom Helper Bar */}
        <div style={{
          position: 'absolute',
          bottom: '10px',
          right: '12px',
          backgroundColor: 'rgba(255, 255, 255, 0.9)',
          padding: '4px 10px',
          borderRadius: '4px',
          border: '1px solid #cbd5e1',
          fontSize: '0.7rem',
          color: '#64748b',
          pointerEvents: 'none'
        }}>
          💡 Click node to inspect • Drag node to rearrange • Scroll to zoom
        </div>
      </div>
    </div>
  );
}

// --- MAIN ASK KNOWLEDGE PAGE COMPONENT ---
function AgentRunProgress({ progress }) {
  const stages = [
    ['query_transform', 'Understand and plan'],
    ['retrieval', 'Retrieve graph evidence'],
    ['rank_fusion', 'Combine retrieval results'],
    ['evidence_reranking', 'Rerank evidence'],
    ['evidence_packet', 'Build cited evidence packet'],
    ['answer_generation', 'Generate answer'],
    ['answer_verification', 'Verify answer support']
  ];
  const events = progress?.events || [];
  const currentIndex = stages.findIndex(([key]) => key === progress?.stage);
  const lastEvent = (key) => [...events].reverse().find(event => event.stage === key)?.event;
  const detailFor = (key) => {
    const event = lastEvent(key);
    if (!event) return '';
    if (key === 'query_transform') return `Intent: ${event.intent || 'classified'} · Tools: ${(event.tools || []).join(', ') || 'selected'}`;
    if (key === 'retrieval') return `${event.evidence_candidates ?? 0} candidate facts · ${event.tools?.join(', ') || 'retrieval tools'}`;
    if (key === 'rank_fusion') return `${event.candidate_count ?? 0} candidates combined`;
    if (key === 'evidence_reranking') return `${event.evidence_count ?? 0} evidence items retained`;
    if (key === 'evidence_packet') return `${event.evidence_count ?? 0} cited items · about ${event.token_estimate ?? 0} tokens`;
    if (key === 'answer_generation') return `Citations: ${event.citations?.length ?? 0} · confidence ${Math.round((event.confidence || 0) * 100)}%`;
    if (key === 'answer_verification') return `${event.supported ? 'Supported' : 'Checking support'} · ${event.complete ? 'complete' : 'may need more evidence'}`;
    return '';
  };

  return (
    <section aria-live="polite" className="card" style={{ padding: '18px 22px', border: '1px solid #bfdbfe', background: '#f8fbff' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', marginBottom: '12px' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: '0.95rem', color: '#0f172a' }}>Agent run status</h3>
          <div style={{ marginTop: '3px', color: '#64748b', fontSize: '0.78rem' }}>
            Live stages reported by the backend · {progress?.request_id ? `Run ${progress.request_id.slice(0, 8)}` : 'Starting request'}
          </div>
        </div>
        <span style={{ color: '#2563eb', fontSize: '0.78rem', fontWeight: 700 }}>
          {progress?.stage === 'completed' ? 'Complete' : 'In progress'}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: '9px' }}>
        {stages.map(([key, label], index) => {
          const complete = progress?.stage === 'completed' || (currentIndex >= 0 && index < currentIndex);
          const active = progress?.stage === key;
          const Icon = complete ? CheckCircle2 : null;
          return (
            <div key={key} style={{ display: 'flex', gap: '9px', alignItems: 'flex-start', padding: '10px', borderRadius: '9px', border: `1px solid ${active ? '#60a5fa' : '#e2e8f0'}`, background: active ? '#eff6ff' : '#fff' }}>
              <span style={{ flexShrink: 0, width: '20px', height: '20px', borderRadius: '50%', display: 'grid', placeItems: 'center', color: complete ? '#059669' : active ? '#2563eb' : '#94a3b8', fontSize: '0.72rem', fontWeight: 800 }}>
                {Icon ? <Icon size={17} /> : active ? '●' : index + 1}
              </span>
              <div>
                <div style={{ color: '#1e293b', fontSize: '0.78rem', fontWeight: 700 }}>{label}</div>
                <div style={{ marginTop: '3px', color: '#64748b', fontSize: '0.7rem', lineHeight: 1.4 }}>
                  {detailFor(key) || (active ? 'Working…' : complete ? 'Done' : 'Waiting')}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {progress?.status === 'failed' && (
        <div role="alert" style={{ color: '#b91c1c', marginTop: '12px', fontSize: '0.8rem' }}>
          Agent stopped at {progress.stage}: {progress.error || 'request failed'}
        </div>
      )}
    </section>
  );
}

export default function AskKnowledgePage() {
  const [query, setQuery] = useState('');
  const [conversationId, setConversationId] = useState(() => {
    try {
      const existing = sessionStorage.getItem('papertrail-ask-conversation-id');
      if (existing) return existing;
      const created = crypto.randomUUID();
      sessionStorage.setItem('papertrail-ask-conversation-id', created);
      return created;
    } catch (_) { return `chat-${Date.now()}`; }
  });
  const [conversationHistory, setConversationHistory] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem('papertrail-ask-history') || '[]');
      return Array.isArray(saved) ? saved.slice(-8) : [];
    } catch (_) { return []; }
  });
  const [retrievalMode, setRetrievalMode] = useState('agent');
  const [includeUnreviewed, setIncludeUnreviewed] = useState(false);
  const [queryProgress, setQueryProgress] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [currentQuestion, setCurrentQuestion] = useState('');
  const [queryError, setQueryError] = useState('');
  const [showPipeline, setShowPipeline] = useState(false);
  const [answerView, setAnswerView] = useState('text'); // 'text' | 'graph' | 'table'
  const [queryExamples, setQueryExamples] = useState([]);

  useEffect(() => {
    try { sessionStorage.setItem('papertrail-ask-history', JSON.stringify(conversationHistory.slice(-8))); }
    catch (_) { /* Session storage may be disabled; current page context still works. */ }
  }, [conversationHistory]);

  useEffect(() => {
    let active = true;
    aiService.getKnowledgeGraph().then(graph => {
      if (!active) return;
      const nodes = graph.nodes || [];
      const customers = nodes.filter(node => node.type === 'Customer');
      const vendors = nodes.filter(node => ['Vendor', 'Supplier', 'Organization', 'Company'].includes(node.type));
      const invoices = nodes.filter(node => node.type === 'Invoice');
      const nameOf = node => node?.data?.name || node?.label || node?.id;
      const customer = customers[0];
      const vendor = vendors.find(node => node.id !== customer?.id);
      const examples = [];
      if (customer) examples.push({ label: 'Customer briefing', question:
        `Prepare an account briefing for ${nameOf(customer)} covering invoice amounts, review status, products, and linked communications.` });
      if (invoices[0]) examples.push({ label: 'Invoice lookup', question:
        `Summarize invoice ${invoices[0].data?.invoice_number || invoices[0].label}: customer, vendor, line items, amount, and review status.` });
      if (vendor && customer) examples.push({ label: 'Vendor relationship', question:
        `Show how ${nameOf(vendor)} is connected to ${nameOf(customer)} through invoices and products.` });
      if (customer) examples.push({ label: 'Email summary', question:
        `Summarize the email conversation linked to ${nameOf(customer)}, including commitments and open actions.` });
      examples.push({ label: 'Review risks', question:
        'Which invoices need review, and what evidence explains why they need attention?' });
      examples.push({ label: 'Invoice count', question: 'How many invoice records are in the knowledge graph?' });
      if (vendors.length > 1) examples.push({ label: 'Compare vendors', question:
        `Compare ${nameOf(vendors[0])} and ${nameOf(vendors[1])} using their invoice activity and linked customers.` });
      if (!examples.length) examples.push({ label: 'Invoice contents', question:
        'What products and line items are recorded in the invoices?' });
      setQueryExamples(examples);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  const handleSearch = async (overrideQuery) => {
    const q = overrideQuery || query;
    if (!q.trim()) return;
    
    setIsLoading(true);
    setQueryError('');
    setQueryProgress(retrievalMode === 'agent'
      ? { status: 'running', stage: 'accepted', events: [] }
      : null);
    try {
      const res = await aiService.queryKnowledge(q, null, retrievalMode, setQueryProgress,
        includeUnreviewed, conversationHistory, conversationId);
      setResult(res);
      setCurrentQuestion(q);
      setConversationHistory(current => [...current,
        { role: 'user', content: q },
        { role: 'assistant', content: res.answer || '' }
      ].filter(message => message.content).slice(-8));
      setAnswerView('text'); // default view is text
    } catch (err) {
      setQueryError(err.message || 'The question could not be completed. Please try again.');
      setQueryProgress(current => ({ ...(current || {}), status: 'failed', error: err.message }));
      console.error("Query failed:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const graphNodes = result?.graph?.nodes || [];
  const graphEdges = result?.graph?.edges || [];
  const tableRows = result?.table || [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', maxWidth: '1000px', margin: '0 auto', width: '100%' }}>
      
      {/* Header */}
      <div style={{ textAlign: 'center', margin: '16px 0 6px 0' }}>
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
          padding: '4px 12px',
          borderRadius: '999px',
          backgroundColor: '#eff6ff',
          color: '#2563eb',
          fontSize: '0.8rem',
          fontWeight: 700,
          marginBottom: '10px'
        }}>
          <Sparkles size={14} />
          <span>Evidence-grounded retrieval · Agent workflow with configured model</span>
        </div>
        <h1 style={{ fontSize: '2.25rem', fontWeight: 800, color: 'var(--text-primary)' }}>
          Ask Knowledge Agent
        </h1>
        <p style={{ fontSize: '1rem', color: 'var(--text-secondary)', marginTop: '4px' }}>
          Query structured facts, entities, and multi-hop relationships extracted directly from your business invoices.
        </p>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', marginTop: '14px', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          Retrieval mode
          <select value={retrievalMode} onChange={event => setRetrievalMode(event.target.value)}
            style={{ padding: '7px 10px', border: '1px solid #cbd5e1', borderRadius: '8px', background: '#fff', color: '#0f172a' }}>
            <option value="agent">Agent · plan, retrieve, verify</option>
            <option value="hybrid">Standard graph retrieval</option>
          </select>
        </label>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', marginTop: '10px', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          <input type="checkbox" checked={includeUnreviewed}
            onChange={event => setIncludeUnreviewed(event.target.checked)} />
          Include unreviewed invoice records
        </label>
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '10px', marginTop: '8px', minHeight: '26px' }}>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
            {conversationHistory.length ? 'Follow-up questions use this chat’s recent context.' : 'Ask a follow-up without repeating the customer name.'}
          </span>
          {conversationHistory.length > 0 && <button className="btn btn-outline btn-sm"
            onClick={() => {
              const created = crypto.randomUUID();
              setConversationId(created);
              try { sessionStorage.setItem('papertrail-ask-conversation-id', created); } catch (_) { }
              setConversationHistory([]);
              setResult(null);
              setCurrentQuestion('');
              setQueryError('');
            }}
            style={{ padding: '3px 9px', fontSize: '0.72rem' }}>
            <RotateCcw size={12} /> New chat
          </button>}
        </div>
      </div>

      {/* Query Search Box */}
      <div className="card" style={{ padding: '8px 12px 8px 20px', display: 'flex', alignItems: 'center', gap: '12px', boxShadow: 'var(--shadow-md)' }}>
        <Sparkles size={20} color="#2563eb" style={{ flexShrink: 0 }} />
        <input 
          type="text"
          placeholder="Ask about sellers, buyers, invoice numbers, products, or values..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          style={{
            border: 'none',
            outline: 'none',
            fontSize: '1rem',
            width: '100%',
            color: 'var(--text-primary)'
          }}
        />
        <button
          className="btn btn-primary"
          onClick={() => handleSearch()}
          disabled={isLoading || !query.trim()}
          style={{ padding: '10px 22px' }}
        >
          <span>{isLoading ? (retrievalMode === 'agent' ? "Running agent..." : "Retrieving...") : "Ask Agent"}</span>
          <Send size={15} />
        </button>
      </div>

      {isLoading && retrievalMode === 'agent' && (
        <AgentRunProgress progress={queryProgress} />
      )}

      <details style={{ alignSelf: 'center', width: '100%', maxWidth: '900px', color: 'var(--text-secondary)' }}>
        <summary style={{ cursor: 'pointer', textAlign: 'center', fontSize: '0.8rem', fontWeight: 700 }}>
          Try a sample question · {queryExamples.length || 'loading'} query types
        </summary>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '8px', paddingTop: '10px' }}>
          {queryExamples.map(example => (
            <button key={example.label} className="btn btn-outline btn-sm"
              title={example.question}
              onClick={() => { setQuery(example.question); handleSearch(example.question); }}
              style={{ backgroundColor: '#ffffff', fontSize: '0.78rem' }}>
              {example.label}
            </button>
          ))}
        </div>
      </details>

      {queryError && <div role="alert" className="card" style={{ padding: '12px 16px', color: '#991b1b', background: '#fef2f2', borderColor: '#fecaca' }}>
        {queryError}
      </div>}

      {/* Query Result Section - ANSWER APPEARS FIRST */}
      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginTop: '10px' }}>

          {conversationHistory.length > 2 && <div aria-label="Earlier chat messages" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {conversationHistory.slice(0, -2).map((message, index) => {
              const isUser = message.role === 'user';
              return <div key={`${index}-${message.role}`} style={{ alignSelf: isUser ? 'flex-end' : 'flex-start', maxWidth: '82%', padding: '10px 14px', borderRadius: '16px', background: isUser ? '#2563eb' : '#f1f5f9', color: isUser ? '#fff' : '#0f172a', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                <div style={{ fontSize: '0.68rem', fontWeight: 800, opacity: 0.75, marginBottom: '3px' }}>{isUser ? 'You' : 'Assistant'}</div>
                {message.content}
              </div>;
            })}
          </div>}
          
          {/* Main Answer Card with 3-Way Form Selector Filter */}
          <div className="card" style={{ padding: '24px 28px', backgroundColor: '#ffffff', borderLeft: '5px solid #2563eb', boxShadow: 'var(--shadow-md)' }}>

            {currentQuestion && <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '14px' }}>
              <div style={{ maxWidth: '82%', padding: '10px 14px', borderRadius: '16px 16px 4px 16px', background: '#2563eb', color: '#fff', lineHeight: 1.5 }}>
                <div style={{ fontSize: '0.68rem', fontWeight: 800, opacity: 0.8, marginBottom: '3px' }}>You</div>
                {currentQuestion}
              </div>
            </div>}
            
            {/* Top Toolbar: Badges + 3-Form View Selector Filter */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '14px', marginBottom: '18px', borderBottom: '1px solid #f1f5f9', paddingBottom: '14px' }}>
              
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span className={`badge ${result.evidence_citations?.length ? 'badge-blue' : 'badge-gray'}`}>{result.evidence_citations?.length ? 'Graph-backed response' : 'No graph sources returned'}</span>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  {result.retrieval_backend ? `Retrieved via ${result.retrieval_backend}` : 'Evidence link is shown when returned by retrieval.'}
                </span>
                {result.model_name && (
                  <span className="badge badge-gray" title={result.answer_mode} style={{ fontSize: '0.68rem' }}>
                    Model: {result.model_name}
                  </span>
                )}
              </div>

              {/* 3 Forms Filter: Text (Default), Knowledge Graph, Table */}
              <div style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                backgroundColor: '#f1f5f9',
                padding: '4px',
                borderRadius: 'var(--radius-md)'
              }}>
                <button
                  onClick={() => setAnswerView('text')}
                  className={`btn btn-sm ${answerView === 'text' ? 'btn-primary' : 'btn-outline'}`}
                  style={{ fontSize: '0.8rem', padding: '4px 12px', display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  <AlignLeft size={14} />
                  <span>Text Answer (Default)</span>
                </button>

                <button
                  onClick={() => setAnswerView('graph')}
                  className={`btn btn-sm ${answerView === 'graph' ? 'btn-primary' : 'btn-outline'}`}
                  style={{ fontSize: '0.8rem', padding: '4px 12px', display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  <Network size={14} />
                  <span>Knowledge Graph</span>
                </button>

                <button
                  onClick={() => setAnswerView('table')}
                  className={`btn btn-sm ${answerView === 'table' ? 'btn-primary' : 'btn-outline'}`}
                  style={{ fontSize: '0.8rem', padding: '4px 12px', display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  <Table size={14} />
                  <span>Table View</span>
                </button>
              </div>

            </div>

            {/* FORM 1: TEXT VIEW (DEFAULT) */}
            {answerView === 'text' && (
              <div style={{ fontSize: '1rem', fontWeight: 500, lineHeight: 1.7, color: '#0f172a', maxWidth: '88%', padding: '14px 18px', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '4px 16px 16px 16px' }}>
                <div style={{ fontSize: '0.7rem', color: '#475569', fontWeight: 800, marginBottom: '5px' }}>PaperTrail Assistant</div>
                {String(result.answer || '').split('\n').map((line, idx) => {
                  const trimmed = line.trim();
                  if (!trimmed) return <div key={idx} style={{ height: '6px' }} />;
                  if (trimmed.startsWith('### ')) return <h3 key={idx} style={{ fontSize: '1.15rem', fontWeight: 800, margin: '12px 0 6px', color: '#1e3a8a' }}>{renderAnswerInline(trimmed.slice(4), 'line-' + idx)}</h3>;
                  const bullet = trimmed.match(/^[-*]\s+(.*)$/);
                  if (bullet) return <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: '9px', margin: '4px 0' }}>
                    <span aria-hidden="true" style={{ color: '#2563eb', fontWeight: 800 }}>-</span>
                    <span>{renderAnswerInline(bullet[1], 'line-' + idx)}</span>
                  </div>;
                  return <p key={idx} style={{ margin: '6px 0' }}>{renderAnswerInline(trimmed, 'line-' + idx)}</p>;
                })}
                {result.evidence_citations?.length > 0 && <div style={{ borderTop: '1px solid #e2e8f0', marginTop: '16px', paddingTop: '10px' }}>
                  <div style={{ fontSize: '0.74rem', color: '#64748b', fontWeight: 800, marginBottom: '6px' }}>EVIDENCE RECORDS</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                    {result.evidence_citations.slice(0, 8).map((source, index) => <span key={source.document_id + '-' + index} className="badge badge-gray" title={source.quote + ' - ' + source.document_id}>{source.source}</span>)}
                    {result.evidence_citations.length > 8 && <span className="badge badge-gray">+{result.evidence_citations.length - 8} more</span>}
                  </div>
                </div>}
              </div>
            )}

            {/* FORM 2: KNOWLEDGE GRAPH VIEW (NEO4J STYLE CANVAS) */}
            {answerView === 'graph' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <InteractiveSubgraph graphData={result.graph} />
              </div>
            )}

            {/* FORM 3: TABLE VIEW */}
            {answerView === 'table' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--text-secondary)' }}>
                    Structured Tabular Records ({tableRows.length} attributes)
                  </span>
                  <span className="badge badge-blue">Tabular View</span>
                </div>

                <div style={{ overflowX: 'auto', border: '1px solid #e2e8f0', borderRadius: 'var(--radius-md)' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                    <thead>
                      <tr style={{ backgroundColor: '#f8fafc', borderBottom: '1px solid #e2e8f0', textAlign: 'left' }}>
                        <th style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text-muted)' }}>Type</th>
                        <th style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text-muted)' }}>Subject / Entity</th>
                        <th style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text-muted)' }}>Property / Predicate</th>
                        <th style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text-muted)' }}>Value / Attribute</th>
                        <th style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text-muted)' }}>Confidence</th>
                        <th style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text-muted)' }}>Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tableRows.map((row, idx) => (
                        <tr key={idx} style={{ borderBottom: '1px solid #f1f5f9', backgroundColor: idx % 2 === 0 ? '#ffffff' : '#fafafa' }}>
                          <td style={{ padding: '10px 14px' }}>
                            <span className={`badge ${row.category === 'Verified Fact' ? 'badge-blue' : 'badge-green'}`} style={{ fontSize: '0.7rem' }}>
                              {row.category}
                            </span>
                          </td>
                          <td style={{ padding: '10px 14px', fontWeight: 700, color: '#0f172a' }}>{row.entity}</td>
                          <td style={{ padding: '10px 14px', color: '#475569' }}>{row.property}</td>
                          <td style={{ padding: '10px 14px', fontWeight: 600, color: '#1e3a8a' }}>{row.value}</td>
                          <td style={{ padding: '10px 14px' }}>
                            <ConfidenceBadge confidence={row.confidence} />
                          </td>
                          <td style={{ padding: '10px 14px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>{row.source}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

          </div>

          {/* Transparent Simulation: "How did we get this answer?" */}
          <div className="card" style={{ padding: '20px', backgroundColor: '#f8fafc' }}>
            <div
              onClick={() => setShowPipeline(!showPipeline)}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <ShieldCheck size={18} color="#2563eb" />
                <h3 style={{ fontSize: '0.95rem', fontWeight: 700 }}>
                  How did we get this answer? (Transparent Traceability Pipeline)
                </h3>
              </div>
              <button className="btn btn-outline btn-sm" style={{ padding: '4px 8px' }}>
                {showPipeline ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
            </div>

            {showPipeline && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '16px' }}>
                {result.pipeline_steps.map(s => (
                  <div
                    key={s.step}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '12px',
                      backgroundColor: '#ffffff',
                      padding: '12px 14px',
                      borderRadius: 'var(--radius-md)',
                      border: '1px solid var(--border-light)'
                    }}
                  >
                    <div style={{
                      width: '24px',
                      height: '24px',
                      borderRadius: 'var(--radius-full)',
                      backgroundColor: '#eff6ff',
                      color: '#2563eb',
                      fontWeight: 700,
                      fontSize: '0.75rem',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0
                    }}>
                      {s.step}
                    </div>

                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 700, fontSize: '0.85rem', color: 'var(--text-primary)' }}>
                        {s.name}
                      </div>
                      <div style={{ fontSize: '0.775rem', color: 'var(--text-muted)' }}>
                        {s.description}
                      </div>
                      <div style={{ fontSize: '0.8rem', color: '#1e3a8a', backgroundColor: '#f0fdf4', padding: '6px 10px', borderRadius: '4px', marginTop: '6px', border: '1px solid #bbf7d0' }}>
                        {s.detail}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Citations & Evidence Records */}
          <div>
            <div style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--text-secondary)', marginBottom: '10px' }}>
              Evidentiary Basis & Document Citations ({result.evidence_citations.length})
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '12px' }}>
              {result.evidence_citations.map((c, idx) => (
                <div key={idx} className="card" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.8rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                      <FileText size={15} color="#2563eb" />
                      <span>{c.source}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                      {c.citation_ids?.map(citation => <span key={citation} className="badge badge-blue">{citation}</span>)}
                      {c.review_status && <span className={`badge ${['approved', 'corrected'].includes(c.review_status) ? 'badge-green' : 'badge-amber'}`}>
                        {c.review_status.replaceAll('_', ' ')}
                      </span>}
                      {c.synthetic && <span className="badge badge-purple">SYNTHETIC DEMO</span>}
                    </div>
                  </div>
                  <div style={{ fontSize: '0.85rem', fontStyle: 'italic', color: '#1e293b', backgroundColor: '#f8fafc', padding: '10px', borderRadius: '6px' }}>
                    "{c.quote}"
                  </div>
                  {c.supporting_emails?.length > 0 && <div style={{ display: 'grid', gap: '8px', marginTop: '4px' }}>
                    {c.supporting_emails.map(email => <div key={email.canonical_id} style={{ borderLeft: '2px solid #cbd5e1', paddingLeft: '10px' }}>
                      <div style={{ display: 'flex', gap: '6px', alignItems: 'center', fontSize: '0.78rem', fontWeight: 700 }}>
                        <span>{email.source}</span>
                        {email.citation_ids?.map(citation => <span key={citation} className="badge badge-blue">{citation}</span>)}
                        {email.synthetic && <span className="badge badge-purple">SYNTHETIC DEMO</span>}
                      </div>
                      {email.quote && <p style={{ fontSize: '0.82rem', lineHeight: 1.45, margin: '5px 0 0' }}>{email.quote}</p>}
                    </div>)}
                  </div>}
                </div>
              ))}
            </div>
          </div>

        </div>
      )}

    </div>
  );
}
