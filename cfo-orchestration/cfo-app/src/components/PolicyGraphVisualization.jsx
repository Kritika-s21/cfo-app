import { useEffect, useId, useMemo, useState } from "react";
import { PolicyAPI } from "../lib/ezcoworker.js";
import PolicyManagement from "./PolicyManagement.jsx";

const RELATION_COLORS = {
  HAS_SECTION: "#6d28d9",
  APPLIES_TO: "#047857",
  REFERENCES: "#1d4ed8",
  SUPERSEDES: "#b45309",
};

const NODE_COLORS = {
  policy: "#1d4ed8",
  agent: "#047857",
  section: "#6d28d9",
};

function nodeKind(node) {
  if (node.type === "agent" || node.id.startsWith("agent:")) return "agent";
  if (node.id.includes("#")) return "section";
  return "policy";
}

function nodeLabel(node) {
  if (node.id.startsWith("agent:")) return node.id.slice("agent:".length);
  return node.id;
}

function edgePath(source, target, sameLane) {
  if (sameLane) {
    const curve = Math.max(54, Math.abs(target.y - source.y) * 0.35);
    return `M ${source.x} ${source.y} C ${source.x + curve} ${source.y}, ${target.x + curve} ${target.y}, ${target.x} ${target.y}`;
  }
  const middleX = (source.x + target.x) / 2;
  return `M ${source.x} ${source.y} C ${middleX} ${source.y}, ${middleX} ${target.y}, ${target.x} ${target.y}`;
}

export default function PolicyGraphVisualization() {
  const markerId = `policy-graph-arrow-${useId().replace(/:/g, "")}`;
  const [activeTab, setActiveTab] = useState("markdown");
  const [graph, setGraph] = useState(null);
  const [knowledgeStatus, setKnowledgeStatus] = useState(null);
  const [error, setError] = useState("");
  const [knowledgeStatusError, setKnowledgeStatusError] = useState("");
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [selectedRelations, setSelectedRelations] = useState(
    () => new Set(Object.keys(RELATION_COLORS)),
  );
  const [selectedNodeId, setSelectedNodeId] = useState("");

  useEffect(() => {
    let active = true;
    PolicyAPI.graph()
      .then((data) => {
        if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) {
          throw new Error("The policy graph endpoint returned invalid graph data.");
        }
        if (active) {
          setGraph(data);
          setError("");
        }
      })
      .catch((requestError) => {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : String(requestError));
        }
      });
    PolicyAPI.status()
      .then((status) => {
        if (active) {
          setKnowledgeStatus(status);
          setKnowledgeStatusError("");
        }
      })
      .catch((statusRequestError) => {
        if (active) {
          setKnowledgeStatusError(statusRequestError instanceof Error ? statusRequestError.message : String(statusRequestError));
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const layout = useMemo(() => {
    if (!graph) return null;

    const lanes = { agent: [], policy: [], section: [] };
    graph.nodes.forEach((node) => {
      const kind = nodeKind(node);
      lanes[kind].push(node);
    });
    Object.values(lanes).forEach((nodes) => nodes.sort((a, b) => a.id.localeCompare(b.id)));

    const rowHeight = 82;
    const height = Math.max(480, Math.max(...Object.values(lanes).map((nodes) => nodes.length), 1) * rowHeight + 80);
    const laneX = { agent: 195, policy: 600, section: 1005 };
    const positions = new Map();

    Object.entries(lanes).forEach(([kind, nodes]) => {
      const laneHeight = nodes.length * rowHeight;
      const firstY = (height - laneHeight) / 2 + rowHeight / 2;
      nodes.forEach((node, index) => {
        positions.set(node.id, { ...node, kind, x: laneX[kind], y: firstY + index * rowHeight });
      });
    });

    return { lanes, positions, height };
  }, [graph]);

  const visibleEdges = useMemo(() => {
    if (!graph || !layout) return [];
    return graph.edges.filter((edge) =>
      selectedRelations.has(edge.rel)
      && layout.positions.has(edge.source)
      && layout.positions.has(edge.target),
    );
  }, [graph, layout, selectedRelations]);

  const selectedNode = layout?.positions.get(selectedNodeId);
  const connectedNodeIds = selectedNode
    ? new Set(visibleEdges.flatMap((edge) => {
      if (edge.source === selectedNodeId) return [edge.target];
      if (edge.target === selectedNodeId) return [edge.source];
      return [];
    }))
    : null;

  const toggleRelation = (relation) => {
    setSelectedRelations((current) => {
      const next = new Set(current);
      if (next.has(relation)) next.delete(relation);
      else next.add(relation);
      return next;
    });
  };

  const changeZoom = (amount) => {
    setZoomLevel((current) => Math.min(2, Math.max(0.5, Math.round((current + amount) * 10) / 10)));
  };

  const runVectorSearch = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setSearchError("");
    try {
      setSearchResults(await PolicyAPI.search(query.trim()));
    } catch (searchRequestError) {
      setSearchError(searchRequestError instanceof Error ? searchRequestError.message : String(searchRequestError));
    } finally {
      setSearching(false);
    }
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden", background: "var(--bg-app, #f8fafc)", color: "var(--text-primary, #1e293b)" }}>
      <nav aria-label="Policy knowledge views" style={{ display: "flex", gap: 4, padding: "10px 20px 0", borderBottom: "1px solid var(--border-default, #e2e8f0)", flexShrink: 0 }}>
        {[["markdown", "Markdown"], ["vectors", "Vectors"], ["graph", "Graph"]].map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={activeTab === key} onClick={() => setActiveTab(key)}
            style={{ padding: "8px 14px", background: "transparent", border: "none", borderBottom: `2px solid ${activeTab === key ? "#1f6feb" : "transparent"}`, color: activeTab === key ? "#1d4ed8" : "#64748b", fontSize: 12, fontWeight: activeTab === key ? 600 : 400, cursor: "pointer", fontFamily: "inherit" }}>
            {label}
          </button>
        ))}
      </nav>

      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {activeTab === "markdown" ? (
          <PolicyManagement />
        ) : activeTab === "vectors" ? (
          <section style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <header style={{ padding: "18px 24px 14px", borderBottom: "1px solid var(--border-default, #e2e8f0)", flexShrink: 0 }}>
              <div style={{ fontSize: 18, fontWeight: 700 }}>Policy Vector Search</div>
              <div style={{ color: "var(--text-muted, #64748b)", fontSize: 12, marginTop: 5 }}>
                Search indexed Markdown chunks using the configured {knowledgeStatus?.vector_backend || "vector"} backend.
                {knowledgeStatus ? ` ${knowledgeStatus.chunks} chunks indexed.` : !knowledgeStatusError ? " Loading index status…" : ""}
              </div>
              {knowledgeStatusError && <div role="alert" style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}>Could not load vector index status: {knowledgeStatusError}</div>}
              <form onSubmit={runVectorSearch} style={{ display: "flex", gap: 8, marginTop: 14 }}>
                <input aria-label="Search policy vectors" value={query} onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search policy content…" style={{ flex: 1, minWidth: 0, padding: "9px 11px", background: "var(--bg-input, #f8fafc)", border: "1px solid var(--border-strong, #cbd5e1)", borderRadius: 6, color: "var(--text-primary, #1e293b)", fontSize: 12, fontFamily: "inherit" }} />
                <button type="submit" disabled={searching || !query.trim()}
                  style={{ padding: "8px 14px", border: "none", borderRadius: 6, background: "#1f6feb", color: "#fff", fontSize: 12, fontWeight: 600, cursor: searching || !query.trim() ? "default" : "pointer", opacity: searching || !query.trim() ? 0.6 : 1 }}>
                  {searching ? "Searching…" : "Search"}
                </button>
              </form>
              <div style={{ color: "var(--text-muted, #64748b)", fontSize: 10, marginTop: 7 }}>Match scores are backend similarity scores, not percentages. Results show the indexed chunks returned by vector search.</div>
            </header>
            <div style={{ flex: 1, overflowY: "auto", padding: "16px 24px" }}>
              {searchError && <div role="alert" style={{ padding: 10, marginBottom: 12, border: "1px solid #dc262644", borderRadius: 6, background: "#dc262612", color: "#b91c1c", fontSize: 12 }}>Vector search failed: {searchError}</div>}
              {searchResults ? searchResults.hits.length ? (
                <div style={{ display: "grid", gap: 10 }}>
                  {searchResults.hits.map((hit) => (
                    <article key={hit.chunk} style={{ padding: 14, background: "var(--bg-card, #ffffff)", border: "1px solid var(--border-default, #e2e8f0)", borderRadius: 8 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 8 }}>
                        <strong style={{ color: "#2563eb", fontSize: 12 }}>{hit.chunk}</strong>
                        <span style={{ color: "#16a34a", fontSize: 11, whiteSpace: "nowrap" }}>Score: {hit.score}</span>
                      </div>
                      <pre style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere", color: "var(--text-secondary, #475569)", fontFamily: "inherit", fontSize: 12, lineHeight: 1.6 }}>{hit.text}</pre>
                    </article>
                  ))}
                  {(searchResults.graph_expanded.length > 0 || searchResults.always_loaded.length > 0) && (
                    <div style={{ color: "var(--text-muted, #64748b)", fontSize: 11, lineHeight: 1.7 }}>
                      {searchResults.graph_expanded.length > 0 && <div>Graph-linked policies: {searchResults.graph_expanded.join(", ")}</div>}
                      {searchResults.always_loaded.length > 0 && <div>Always-loaded policies: {searchResults.always_loaded.join(", ")}</div>}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ color: "var(--text-muted, #64748b)", fontSize: 12 }}>No matching vector chunks were returned.</div>
              ) : (
                <div style={{ color: "var(--text-muted, #64748b)", fontSize: 12 }}>Enter a query to search the indexed policy chunks.</div>
              )}
            </div>
          </section>
        ) : (
          <section style={{ display: "flex", flexDirection: "column", minHeight: 0, height: "100%", background: "var(--bg-app, #f8fafc)", color: "var(--text-primary, #1e293b)" }}>
            {error ? (
              <div role="alert" style={{ margin: 16, padding: 16, border: "1px solid #dc262644", borderRadius: 8, background: "#dc262612", color: "#b91c1c", fontSize: 13 }}>
                Could not load policy graph: {error}
              </div>
            ) : !graph || !layout ? (
              <div style={{ padding: 16, color: "var(--text-muted, #64748b)", fontSize: 13 }}>Loading policy graph…</div>
            ) : (
              <>
                <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, padding: "12px 16px", borderBottom: "1px solid var(--border-default, #e2e8f0)" }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 700 }}>Policy Knowledge Graph</div>
                    <div style={{ color: "var(--text-muted, #64748b)", fontSize: 11, marginTop: 3 }}>{graph.nodes.length} nodes · {graph.edges.length} edges</div>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "flex-end", alignItems: "center", gap: 6 }}>
                    <div role="group" aria-label="Graph zoom controls" style={{ display: "flex", alignItems: "center", gap: 5, marginRight: 4 }}>
                      <button type="button" aria-label="Zoom out" title="Zoom out" disabled={zoomLevel <= 0.5} onClick={() => changeZoom(-0.1)}
                        style={{ width: 28, height: 26, border: "1px solid var(--border-strong, #cbd5e1)", borderRadius: 5, background: "var(--bg-subtle, #f8fafc)", color: "var(--text-primary, #1e293b)", fontSize: 16, cursor: zoomLevel <= 0.5 ? "default" : "pointer", opacity: zoomLevel <= 0.5 ? 0.5 : 1 }}>−</button>
                      <span aria-live="polite" style={{ minWidth: 38, textAlign: "center", color: "var(--text-secondary, #475569)", fontSize: 10 }}>{Math.round(zoomLevel * 100)}%</span>
                      <button type="button" aria-label="Zoom in" title="Zoom in" disabled={zoomLevel >= 2} onClick={() => changeZoom(0.1)}
                        style={{ width: 28, height: 26, border: "1px solid var(--border-strong, #cbd5e1)", borderRadius: 5, background: "var(--bg-subtle, #f8fafc)", color: "var(--text-primary, #1e293b)", fontSize: 16, cursor: zoomLevel >= 2 ? "default" : "pointer", opacity: zoomLevel >= 2 ? 0.5 : 1 }}>+</button>
                      <button type="button" aria-label="Reset zoom" title="Reset zoom" disabled={zoomLevel === 1} onClick={() => setZoomLevel(1)}
                        style={{ padding: "5px 7px", border: "1px solid var(--border-strong, #cbd5e1)", borderRadius: 5, background: "var(--bg-subtle, #f8fafc)", color: "var(--text-muted, #64748b)", fontSize: 10, cursor: zoomLevel === 1 ? "default" : "pointer", opacity: zoomLevel === 1 ? 0.5 : 1 }}>Reset</button>
                    </div>
                    {Object.entries(RELATION_COLORS).map(([relation, color]) => {
                      const active = selectedRelations.has(relation);
                      return (
                        <button
                          key={relation}
                          type="button"
                          aria-pressed={active}
                          onClick={() => toggleRelation(relation)}
                          style={{ border: `1px solid ${active ? color : "var(--border-strong, #cbd5e1)"}`, borderRadius: 999, padding: "4px 8px", background: active ? `${color}18` : "transparent", color: active ? color : "var(--text-muted, #64748b)", fontSize: 10, cursor: "pointer" }}
                        >
                          {relation.replace(/_/g, " ")}
                        </button>
                      );
                    })}
                  </div>
                </header>

                <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
                  {graph.nodes.length === 0 ? (
                    <div style={{ padding: 20, color: "var(--text-muted, #64748b)", fontSize: 13 }}>No policy graph data is available.</div>
                  ) : (
                    <svg
                      role="img"
                      aria-label="Policy graph showing relationships between policies, agents, and policy sections"
                      viewBox={`0 0 1200 ${layout.height}`}
                      style={{ display: "block", width: `${zoomLevel * 100}%`, minWidth: `${zoomLevel * 840}px`, height: "auto" }}
                    >
                      <defs>
                        <marker id={markerId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                          <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b" />
                        </marker>
                      </defs>

                      <g aria-hidden="true">
                        {[
                          ["Agents", 195, "#047857"],
                          ["Policies", 600, "#1d4ed8"],
                          ["Sections", 1005, "#6d28d9"],
                        ].map(([label, x, color]) => (
                          <text key={label} x={x} y="28" fill={color} textAnchor="middle" fontSize="12" fontWeight="700" letterSpacing="1.2">
                            {label.toUpperCase()}
                          </text>
                        ))}
                      </g>

                      <g>
                        {visibleEdges.map((edge, index) => {
                          const source = layout.positions.get(edge.source);
                          const target = layout.positions.get(edge.target);
                          const touchesSelection = !selectedNode || edge.source === selectedNodeId || edge.target === selectedNodeId;
                          const edgeColor = RELATION_COLORS[edge.rel] || "#64748b";
                          return (
                            <g key={`${edge.source}-${edge.rel}-${edge.target}-${index}`} opacity={touchesSelection ? 0.78 : 0.12}>
                              <path
                                d={edgePath(source, target, source.kind === target.kind)}
                                fill="none"
                                stroke={edgeColor}
                                strokeWidth={touchesSelection ? 1.6 : 1}
                                markerEnd={`url(#${markerId})`}
                              >
                                <title>{`${edge.source} ${edge.rel} ${edge.target}`}</title>
                              </path>
                            </g>
                          );
                        })}
                      </g>

                      <g>
                        {[...layout.positions.values()].map((node) => {
                          const isSelected = node.id === selectedNodeId;
                          const isConnected = connectedNodeIds?.has(node.id);
                          const dimmed = selectedNode && !isSelected && !isConnected;
                          const color = NODE_COLORS[node.kind];
                          const label = nodeLabel(node);
                          const displayLabel = label.length > 23 ? `${label.slice(0, 20)}…` : label;

                          return (
                            <g
                              key={node.id}
                              role="button"
                              tabIndex="0"
                              aria-label={`${node.kind}: ${label}`}
                              aria-pressed={isSelected}
                              onClick={() => setSelectedNodeId(isSelected ? "" : node.id)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                  event.preventDefault();
                                  setSelectedNodeId(isSelected ? "" : node.id);
                                }
                              }}
                              style={{ cursor: "pointer", outline: "none" }}
                              opacity={dimmed ? 0.3 : 1}
                            >
                              <title>{`${node.kind}: ${label}`}</title>
                              <rect
                                x={node.x - 82}
                                y={node.y - 23}
                                width="164"
                                height="46"
                                rx="9"
                                fill={isSelected ? `${color}25` : "var(--bg-card, #ffffff)"}
                                stroke={isSelected ? color : `${color}88`}
                                strokeWidth={isSelected ? 2 : 1}
                              />
                              <circle cx={node.x - 68} cy={node.y} r="4" fill={color} />
                              <text x={node.x - 57} y={node.y + 4} fill="var(--text-primary, #1e293b)" fontSize="11" fontWeight="600">
                                {displayLabel}
                              </text>
                              <text x={node.x + 73} y={node.y + 4} fill={color} textAnchor="end" fontSize="8">
                                {node.kind}
                              </text>
                            </g>
                          );
                        })}
                      </g>
                    </svg>
                  )}
                </div>

                <footer style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8, padding: "9px 14px", borderTop: "1px solid var(--border-default, #e2e8f0)", color: "var(--text-muted, #64748b)", fontSize: 10 }}>
                  <span>Click a node to highlight its connected relationships. Toggle relationship types to filter edges.</span>
                  {selectedNode && <span style={{ color: NODE_COLORS[selectedNode.kind] }}>Selected: {nodeLabel(selectedNode)}</span>}
                </footer>
              </>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
