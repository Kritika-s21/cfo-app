import React, { useState, useEffect } from 'react';
import { Layers, Shield, Settings, Sliders, CheckCircle, Play, FileCode, Cpu, Terminal, X, RefreshCw } from 'lucide-react';

const CATEGORIES = [
  "All", "Orchestration", "Consolidation", "Tax & Compliance", 
  "Operations", "Month-End Close", "Reporting", "Treasury", "QA"
];

// Aesthetic matching for specific agent components based on structural identity
const getAgentVisuals = (slug) => {
  switch (slug) {
    case 'dispatch': return { border: 'border-amber-500/30 hover:border-amber-500/80', iconColor: 'text-amber-400' };
    case 'gl_harmonizer': case 'entity_consolidator': case 'segment_mapper':
      return { border: 'border-blue-500/30 hover:border-blue-500/80', iconColor: 'text-blue-400' };
    case 'gst_engine': case 'tds_engine': case 'tp_monitor':
      return { border: 'border-yellow-600/30 hover:border-yellow-600/80', iconColor: 'text-yellow-500' };
    case 'reconciliation': case 'expense_triage': case 'fixed_asset': case 'ap_engine': case 'ar_engine':
      return { border: 'border-emerald-500/30 hover:border-emerald-500/80', iconColor: 'text-emerald-400' };
    case 'close_orchestrator': case 'je_factory':
      return { border: 'border-teal-500/30 hover:border-teal-500/80', iconColor: 'text-teal-400' };
    case 'financial_analyst': case 'rev_recognition':
      return { border: 'border-pink-500/30 hover:border-pink-500/80', iconColor: 'text-pink-400' };
    case 'cash_forecaster': case 'wc_optimizer':
      return { border: 'border-indigo-500/30 hover:border-indigo-500/80', iconColor: 'text-indigo-400' };
    default: return { border: 'border-cyan-500/30 hover:border-cyan-500/80', iconColor: 'text-cyan-400' };
  }
};

export default function App() {
  const [agents, setAgents] = useState([]);
  const [selectedCategory, setSelectedCategory] = useState("All");
  const [activeAgent, setActiveAgent] = useState(null);
  const [skillMD, setSkillMD] = useState("");
  const [promptInput, setPromptInput] = useState("");
  const [executionLog, setExecutionLog] = useState(null);
  const [executing, setExecuting] = useState(false);

  useEffect(() => {
    fetchAgents();
  }, [selectedCategory]);

  const fetchAgents = async () => {
    let url = 'http://localhost:8000/api/v1/agents';
    if (selectedCategory !== "All") {
      url += `?category=${encodeURIComponent(selectedCategory)}`;
    }
    try {
      const res = await fetch(url);
      const data = await res.json();
      setAgents(data);
    } catch (err) {
      console.error("Error retrieving cluster agents.", err);
    }
  };

  const handleSelectAgent = (agent) => {
    setActiveAgent(agent);
    setSkillMD(agent.skill_markdown || `# ${agent.name} Profile\nExecuted by default configurations.`);
    setExecutionLog(null);
  };

  const saveSkillModification = async () => {
    if (!activeAgent) return;
    try {
      const res = await fetch(`http://localhost:8000/api/v1/agents/${activeAgent.id}/skill`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_markdown: skillMD })
      });
      const updated = await res.json();
      setActiveAgent(updated);
      fetchAgents();
      alert(`SKILL.md config updated successfully for ${updated.name}`);
    } catch (err) {
      alert("Error saving runtime skill modification parameters.");
    }
  };

  const triggerAgentRun = async () => {
    if (!activeAgent || !promptInput.trim()) return;
    setExecuting(true);
    try {
      const res = await fetch('http://localhost:8000/api/v1/agents/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: activeAgent.id,
          prompt: promptInput,
          runtime_override_md: skillMD
        })
      });
      const logOut = await res.json();
      setExecutionLog(logOut);
      fetchAgents(); // Refresh core run counter
    } catch (err) {
      console.error(err);
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0d1117] text-slate-100 font-sans antialiased selection:bg-indigo-500/30">
      {/* Top Controls Filter Workspace */}
      <header className="p-6 border-b border-slate-800 bg-[#161b22]/60 backdrop-blur sticky top-0 z-40">
        <div className="max-w-[1800px] mx-auto flex flex-col gap-4">
          <div className="flex justify-between items-center">
            <div>
              <div className="text-xs font-semibold uppercase tracking-widest text-indigo-400">EzDataMunch Execution Fabric</div>
              <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                CFO Back-Office Node Control Panel <span className="text-xs bg-slate-800 px-2 py-0.5 rounded text-slate-400 font-mono">19 Registered Agents</span>
              </h1>
            </div>
          </div>
          
          {/* Navigation Categories Row matching image_d3ac59.png */}
          <div className="flex flex-wrap gap-2 pt-2 overflow-x-auto scrollbar-none">
            {CATEGORIES.map((cat) => (
              <button
                key={cat}
                onClick={() => setSelectedCategory(cat)}
                className={`px-4 py-1.5 text-xs font-medium rounded-full border transition-all whitespace-nowrap ${
                  selectedCategory === cat
                    ? 'bg-indigo-600 border-indigo-400 text-white font-semibold shadow-md'
                    : 'bg-[#161b22] border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Main Grid View */}
      <main className="p-6 max-w-[1800px] mx-auto grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        {/* Workspace Agents Grid view */}
        <section className={`${activeAgent ? 'xl:col-span-8' : 'xl:col-span-12'} grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 transition-all duration-300`}>
          {agents.map((agent) => {
            const look = getAgentVisuals(agent.slug);
            return (
              <div
                key={agent.id}
                onClick={() => handleSelectAgent(agent)}
                className={`bg-[#161b22] rounded-xl p-5 border cursor-pointer transform hover:-translate-y-0.5 transition-all flex flex-col justify-between group ${look.border} ${activeAgent?.id === agent.id ? 'ring-2 ring-indigo-500 border-transparent bg-[#1f2631]' : ''}`}
              >
                <div>
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className={`p-2 bg-slate-900 rounded-lg border border-slate-800 group-hover:scale-105 transition-transform ${look.iconColor}`}>
                        <Cpu className="h-5 w-5" />
                      </div>
                      <div>
                        <h3 className="font-bold text-[15px] text-white tracking-wide group-hover:text-indigo-300 transition-colors">{agent.name}</h3>
                        <span className="text-[11px] font-mono text-slate-500">{agent.slug}</span>
                      </div>
                    </div>
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-950/50 text-emerald-400 border border-emerald-800/40 flex items-center gap-1">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" /> {agent.status}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 leading-relaxed min-h-[36px] line-clamp-2">{agent.description}</p>
                </div>

                <div className="mt-5 pt-3 border-t border-slate-800/60 flex items-center justify-between">
                  <div className="flex flex-wrap gap-1">
                    {agent.policies.map((p) => (
                      <span key={p} className="text-[9px] font-mono font-bold bg-amber-950/40 text-amber-500 px-1.5 py-0.5 rounded border border-amber-800/30">
                        {p}
                      </span>
                    ))}
                  </div>
                  <span className="text-[11px] font-mono text-slate-500 shrink-0">
                    <strong className="text-slate-300 font-medium">{agent.runs.toLocaleString()}</strong> runs
                  </span>
                </div>
              </div>
            );
          })}
        </section>

        {/* Dynamic Context Modification Inspector Sidebar */}
        {activeAgent && (
          <aside className="xl:col-span-4 bg-[#161b22] border border-slate-800 rounded-xl p-5 sticky top-36 shadow-2xl space-y-6 max-h-[82vh] overflow-y-auto animate-fadeIn">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <Settings className="text-indigo-400 h-4 w-4" />
                <h2 className="font-bold text-white text-sm uppercase tracking-wider">Agent Settings Engine</h2>
              </div>
              <button onClick={() => setActiveAgent(null)} className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-white">
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* General Overview Metrics */}
            <div>
              <h3 className="text-base font-bold text-white">{activeAgent.name}</h3>
              <p className="text-xs text-slate-400 mt-1">{activeAgent.description}</p>
              <div className="mt-3 flex items-center gap-4 text-xs font-mono">
                <div><span className="text-slate-500">Model:</span> <span className="text-indigo-300">{activeAgent.llm_model}</span></div>
                <div><span className="text-slate-500">Temp:</span> <span className="text-indigo-300">{activeAgent.temperature}</span></div>
              </div>
            </div>

            {/* SKILL.md Markdown Configuration Editor */}
            <div className="space-y-2">
              <label className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <FileCode className="h-3.5 w-3.5" /> Core Skill Profile Setup (SKILL.md)
              </label>
              <textarea
                value={skillMD}
                onChange={(e) => setSkillMD(e.target.value)}
                rows={6}
                className="w-full bg-[#0d1117] border border-slate-800 rounded-lg p-3 font-mono text-xs text-slate-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
              <button
                onClick={saveSkillModification}
                className="w-full bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs py-2 rounded-lg font-medium transition-all"
              >
                Commit SKILL.md Mod Changes
              </button>
            </div>

            {/* Real-time Dynamic Tester Hook */}
            <div className="space-y-3 pt-2 border-t border-slate-800">
              <label className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Terminal className="h-3.5 w-3.5" /> Dynamic Execution Trigger
              </label>
              <input
                type="text"
                value={promptInput}
                onChange={(e) => setPromptInput(e.target.value)}
                placeholder="Pass explicit execution command payload..."
                className="w-full bg-[#0d1117] border border-slate-800 rounded-lg p-2.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
              <button
                onClick={triggerAgentRun}
                disabled={executing || !promptInput.trim()}
                className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 text-white text-xs py-2.5 rounded-lg font-medium transition-all flex items-center justify-center gap-2 shadow-md shadow-indigo-600/10"
              >
                {executing ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3 fill-current" />} Run Orchestrated Agent
              </button>
            </div>

            {/* Execution Trace Feed Output */}
            {executionLog && (
              <div className="bg-[#0d1117] border border-slate-800 rounded-lg p-4 font-mono text-[11px] text-slate-300 space-y-2 animate-fadeIn">
                <div className="flex items-center justify-between text-[10px] border-b border-slate-800 pb-1.5 mb-2">
                  <span className="text-emerald-400 font-bold flex items-center gap-1">
                    <CheckCircle className="h-3 w-3" /> TRACE OK
                  </span>
                  <span className="text-slate-500">{executionLog.task_id}</span>
                </div>
                <p><span className="text-slate-500">Status:</span> {executionLog.status}</p>
                <p><span className="text-slate-500">Loaded Policies:</span> {JSON.stringify(executionLog.evaluation_context.loaded_policies)}</p>
                <div className="mt-2 bg-slate-950 p-2 rounded border border-slate-900 max-h-36 overflow-y-auto text-[10px] text-indigo-300">
                  <pre className="whitespace-pre-wrap">{executionLog.evaluation_context.skill_profile_used}</pre>
                </div>
              </div>
            )}
          </aside>
        )}
      </main>
    </div>
  );
}