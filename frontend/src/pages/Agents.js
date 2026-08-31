import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import { Robot, Trash, Plus, X, ShieldWarning, CheckCircle, Lightning } from "@phosphor-icons/react";
import api, { fmt } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const SCOPES = ["read", "request", "sign", "execute", "admin"];
const RISK = { LOW: "text-emerald-400 bg-emerald-500/10", MED: "text-amber-400 bg-amber-500/10", HIGH: "text-red-400 bg-red-500/10" };
const riskUX = { LOW: "✓ Autorisé automatiquement", MED: "⚠ Permission requise", HIGH: "🔐 Confirmation humaine requise" };

export default function Agents() {
  const { checkAuth } = useAuth();
  const [tab, setTab] = useState("agents");
  const [skills, setSkills] = useState([]);
  const [agents, setAgents] = useState([]);
  const [intents, setIntents] = useState([]);
  const [audit, setAudit] = useState([]);
  const [show, setShow] = useState(false);
  const [name, setName] = useState(""); const [scopes, setScopes] = useState(["read"]);
  const [limit, setLimit] = useState("100"); const [ttl, setTtl] = useState("24");
  const [confirmIntent, setConfirmIntent] = useState(null);

  const load = async () => {
    const [sk, ag, it, au] = await Promise.all([
      api.get("/admin/skills"), api.get("/admin/agents"), api.get("/admin/intents"), api.get("/admin/audit")]);
    setSkills(sk.data); setAgents(ag.data); setIntents(it.data); setAudit(au.data);
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!name) return toast.error("Nom requis");
    try {
      await api.post("/admin/agents", { name, scopes, spending_limit_cc: parseFloat(limit) || 0, session_ttl_hours: parseInt(ttl) || 24 });
      toast.success("Agent créé"); setShow(false); setName(""); setScopes(["read"]); await load();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
  };
  const revoke = async (id) => { try { await api.post(`/admin/agents/${id}/revoke`); toast.success("Agent révoqué"); await load(); } catch { toast.error("Erreur"); } };

  const simulate = async (a) => {
    try {
      const r = await api.post("/agent/intent", { skill: "Payments.Send", params: { to: "FREK-DEMO-0001", amount_cc: 50 } },
        { headers: { "X-Agent-Token": a.agent_token } });
      toast.success(`Intent créé (${r.data.status})`); await load();
    } catch (e) { toast.error(e.response?.data?.detail || "Refusé"); await load(); }
  };

  const decide = async (intent, action) => {
    try {
      if (action === "decline") { await api.post(`/agent/intent/${intent.intent_id}/decline`); toast.success("Transaction refusée"); }
      else {
        await api.post(`/agent/intent/${intent.intent_id}/confirm`);
        const agent = agents.find((x) => x.agent_id === intent.agent_id);
        toast.message("Confirmé — exécution…");
        const r = await api.post(`/agent/intent/${intent.intent_id}/execute`, {}, { headers: { "X-Agent-Token": agent?.agent_token } });
        toast.success(`Exécuté · ${r.data.result?.amount_cc ?? ""} CC`); await checkAuth();
      }
      setConfirmIntent(null); await load();
    } catch (e) { toast.error(e.response?.data?.detail || "Échec"); setConfirmIntent(null); await load(); }
  };

  const badge = (s) => ({ prepared: "bg-white/10 text-zinc-300", awaiting_confirmation: "bg-amber-500/15 text-amber-400", confirmed: "bg-cyan-500/15 text-cyan-300", executed: "bg-emerald-500/15 text-emerald-400", denied: "bg-red-500/15 text-red-400" }[s] || "bg-white/10");
  const inputCls = "w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-violet-500 text-white";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Agents IA</h1>
        <p className="text-zinc-400 mt-1">CVLN Agent Factory · pilotage sécurisé des capacités Wallet.</p>
      </div>

      <div className="flex gap-2 flex-wrap">
        {[["agents", "Agents"], ["skills", "Skills"], ["intents", "Intents"], ["audit", "Audit"]].map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} data-testid={`tab-${k}`}
            className={`px-4 py-2.5 rounded-full text-sm font-semibold transition-colors ${tab === k ? "bg-violet-600 text-white" : "bg-white/5 text-zinc-400 hover:text-white"}`}>{l}</button>
        ))}
        {tab === "agents" && <button onClick={() => setShow(true)} data-testid="create-agent-btn" className="ml-auto px-4 py-2.5 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold text-sm flex items-center gap-2"><Plus size={16} weight="bold" /> Créer un agent</button>}
      </div>

      {tab === "agents" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {agents.map((a) => (
            <div key={a.agent_id} className="rounded-2xl border border-white/10 bg-[#12121A] p-5" data-testid={`agent-${a.agent_id}`}>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3"><span className="w-10 h-10 rounded-xl bg-violet-500/15 text-violet-300 flex items-center justify-center"><Robot size={20} weight="duotone" /></span>
                  <div><div className="font-semibold">{a.name}</div><div className="text-xs text-zinc-500">Limite {fmt(a.spending_limit_cc)} CC</div></div></div>
                <span className={`text-[11px] font-semibold px-2 py-1 rounded-full ${a.active ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>{a.active ? "Actif" : "Révoqué"}</span>
              </div>
              <div className="flex flex-wrap gap-1.5 mt-3">{a.scopes.map((s) => <span key={s} className="text-[11px] px-2 py-0.5 rounded bg-white/5 text-zinc-300 uppercase">{s}</span>)}</div>
              <div className="flex gap-2 mt-4">
                <button onClick={() => simulate(a)} disabled={!a.active} className="flex-1 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-sm font-semibold disabled:opacity-40" data-testid={`sim-${a.agent_id}`}>Simuler Payments.Send (50 CC)</button>
                {a.active && <button onClick={() => revoke(a.agent_id)} className="px-3 py-2 rounded-lg bg-red-500/15 text-red-300" data-testid={`revoke-${a.agent_id}`}><Trash size={16} /></button>}
              </div>
            </div>
          ))}
          {agents.length === 0 && <div className="text-zinc-500 py-8">Aucun agent. Créez-en un.</div>}
        </div>
      )}

      {tab === "skills" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {skills.map((s) => (
            <div key={s.name} className="rounded-2xl border border-white/10 bg-[#12121A] p-5" data-testid={`skill-${s.name}`}>
              <div className="flex items-center justify-between"><span className="font-mono font-semibold text-cyan-300">{s.name}</span><span className={`text-[11px] font-semibold px-2 py-1 rounded-full ${RISK[s.risk]}`}>{s.risk}</span></div>
              <p className="text-sm text-zinc-400 mt-2">{s.desc}</p>
              <div className="flex flex-wrap gap-1.5 mt-3">{s.scopes.map((x) => <span key={x} className="text-[11px] px-2 py-0.5 rounded bg-white/5 uppercase text-zinc-300">{x}</span>)}</div>
              <div className="text-xs text-zinc-500 mt-3">{riskUX[s.risk]} · Réseaux : {s.networks.join(", ") || "—"}</div>
            </div>
          ))}
        </div>
      )}

      {tab === "intents" && (
        <div className="rounded-3xl border border-white/10 bg-[#12121A] p-4 sm:p-6 divide-y divide-white/5">
          {intents.map((it) => (
            <div key={it.intent_id} className="py-3 flex items-center gap-3 flex-wrap" data-testid={`intent-${it.intent_id}`}>
              <span className={`text-[11px] font-semibold px-2 py-1 rounded ${RISK[it.risk]}`}>{it.risk}</span>
              <div className="flex-1 min-w-0"><div className="font-mono text-sm text-cyan-300">{it.skill}</div>
                <div className="text-xs text-zinc-500">{it.preview?.amount_cc ? `${fmt(it.preview.amount_cc)} CC → ${it.preview.to}` : it.preview?.capability}</div></div>
              <span className={`text-[11px] font-semibold px-2 py-1 rounded-full ${badge(it.status)}`}>{it.status}</span>
              {it.status === "awaiting_confirmation" && <button onClick={() => setConfirmIntent(it)} className="px-3 py-1.5 rounded-lg bg-violet-600 text-sm font-semibold" data-testid={`review-${it.intent_id}`}>Examiner</button>}
            </div>
          ))}
          {intents.length === 0 && <div className="text-zinc-500 py-8 text-center">Aucun intent</div>}
        </div>
      )}

      {tab === "audit" && (
        <div className="rounded-3xl border border-white/10 bg-[#12121A] p-4 sm:p-6 divide-y divide-white/5">
          {audit.map((a) => (
            <div key={a.log_id} className="py-2.5 flex items-center gap-3 text-sm" data-testid="audit-row">
              <span className="text-xs text-zinc-500 w-32 shrink-0">{new Date(a.created_at).toLocaleString("fr-FR")}</span>
              <span className="font-mono text-violet-300">{a.action}</span>
              <span className="text-zinc-500 truncate">{JSON.stringify(a.detail)}</span>
            </div>
          ))}
          {audit.length === 0 && <div className="text-zinc-500 py-8 text-center">Aucun événement</div>}
        </div>
      )}

      {/* Create agent modal */}
      <AnimatePresence>
        {show && (
          <Modal title="Créer un agent" onClose={() => setShow(false)}>
            <div className="space-y-3">
              <input className={inputCls} placeholder="Nom de l'agent" value={name} onChange={(e) => setName(e.target.value)} data-testid="agent-name" />
              <div>
                <div className="text-xs text-zinc-500 mb-2">Permissions (least-privilege)</div>
                <div className="flex flex-wrap gap-2">
                  {SCOPES.map((s) => {
                    const on = scopes.includes(s); const danger = s === "execute" || s === "admin";
                    return <button key={s} onClick={() => setScopes(on ? scopes.filter((x) => x !== s) : [...scopes, s])} data-testid={`scope-${s}`}
                      className={`px-3 py-1.5 rounded-lg text-sm font-semibold uppercase ${on ? (danger ? "bg-red-500/20 text-red-300" : "bg-violet-600 text-white") : "bg-white/5 text-zinc-400"}`}>{s}</button>;
                  })}
                </div>
              </div>
              <div className="flex gap-2">
                <input className={inputCls} type="number" placeholder="Plafond (CC)" value={limit} onChange={(e) => setLimit(e.target.value)} data-testid="agent-limit" />
                <input className={inputCls} type="number" placeholder="Session (h)" value={ttl} onChange={(e) => setTtl(e.target.value)} data-testid="agent-ttl" />
              </div>
              <button onClick={create} className="w-full py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold" data-testid="agent-submit">Créer l'agent</button>
            </div>
          </Modal>
        )}
        {confirmIntent && (
          <Modal title="Transaction d'un agent" onClose={() => setConfirmIntent(null)}>
            <div className="text-center mb-4"><ShieldWarning size={40} weight="duotone" className="text-red-400 mx-auto" /></div>
            <div className="space-y-2 text-sm bg-white/5 rounded-2xl p-4">
              {[["Agent", agents.find((a) => a.agent_id === confirmIntent.agent_id)?.name],
                ["Action", confirmIntent.skill], ["Montant", `${fmt(confirmIntent.preview?.amount_cc)} CC`],
                ["Destination", confirmIntent.preview?.to], ["Risque", confirmIntent.risk]].map(([k, v]) => (
                <div key={k} className="flex justify-between"><span className="text-zinc-500">{k}</span><span className="font-semibold">{v}</span></div>
              ))}
            </div>
            <p className="text-xs text-zinc-400 mt-3 text-center">🔐 Cette action requiert votre confirmation. Aucun fonds ne bouge sans elle.</p>
            <div className="flex gap-3 mt-5">
              <button onClick={() => decide(confirmIntent, "decline")} className="flex-1 py-3 rounded-full bg-white/10 font-semibold" data-testid="decline-tx">Refuser</button>
              <button onClick={() => decide(confirmIntent, "confirm")} className="flex-1 py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold" data-testid="confirm-tx">Confirmer</button>
            </div>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  );
}

function Modal({ children, title, onClose }) {
  return (
    <motion.div className="fixed inset-0 z-50 flex items-center justify-center p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <motion.div initial={{ scale: 0.95, y: 10 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, opacity: 0 }} className="relative w-full max-w-md bg-[#12121A] border border-white/10 rounded-3xl p-7">
        <div className="flex items-center justify-between mb-5"><h3 className="font-display text-xl font-bold">{title}</h3><button onClick={onClose} className="text-zinc-500 hover:text-white"><X size={22} /></button></div>
        {children}
      </motion.div>
    </motion.div>
  );
}
