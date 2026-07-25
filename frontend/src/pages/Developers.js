import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { Copy, Key, ArrowsClockwise, Code, Eye, EyeSlash, Terminal } from "@phosphor-icons/react";
import api, { fmt, API } from "@/lib/api";

export default function Developers() {
  const [entities, setEntities] = useState([]);
  const [selected, setSelected] = useState(null);
  const [reveal, setReveal] = useState({});

  const load = async () => {
    const data = (await api.get("/entities")).data;
    setEntities(data);
    if (!selected && data.length) setSelected(data[0]);
    else if (selected) setSelected(data.find((e) => e.entity_id === selected.entity_id) || data[0]);
  };
  useEffect(() => { load(); }, []);

  const copy = (t, label = "Copié") => { navigator.clipboard.writeText(t); toast.success(label); };

  const rotate = async (id) => {
    try { await api.post(`/entities/${id}/rotate-key`); toast.success("Clé régénérée"); await load(); }
    catch { toast.error("Erreur"); }
  };

  const s = selected;
  const base = `${API}/v1`;
  const curlTransfer = s ? `curl -X POST "${base}/entity/transfer" \\
  -H "X-API-Key: ${s.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{"to":"FREK-XXXX-1234","amount":500,"note":"Royalties"}'` : "";
  const curlBalance = s ? `curl "${base}/entity/balance" -H "X-API-Key: ${s.api_key}"` : "";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">API Développeurs</h1>
        <p className="text-zinc-400 mt-1">Chaque entité possède son wallet et une clé API pour se connecter à CVLN Fintech.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Entity list */}
        <div className="lg:col-span-1 rounded-3xl border border-white/10 bg-[#12121A] p-4 max-h-[70vh] overflow-auto">
          <div className="text-xs font-semibold tracking-widest uppercase text-zinc-500 px-2 py-2">Entités ({entities.length})</div>
          {entities.map((e) => (
            <button key={e.entity_id} onClick={() => setSelected(e)} data-testid={`entity-${e.entity_id}`}
              className={`w-full text-left px-3 py-3 rounded-xl transition-colors ${s?.entity_id === e.entity_id ? "bg-white/10" : "hover:bg-white/5"}`}>
              <div className="font-semibold text-sm">{e.name}</div>
              <div className="text-xs text-zinc-500">{e.layer} · {fmt(e.balance_cc)} CC</div>
            </button>
          ))}
        </div>

        {/* Detail */}
        {s && (
          <motion.div key={s.entity_id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="lg:col-span-2 space-y-5">
            <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6">
              <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                  <h2 className="font-display text-2xl font-bold">{s.name}</h2>
                  <p className="text-sm text-zinc-500">{s.role}</p>
                </div>
                <div className="text-right">
                  <div className="text-xs uppercase tracking-widest text-zinc-500">Solde wallet</div>
                  <div className="font-display text-xl font-black text-violet-400">{fmt(s.balance_cc)} CC</div>
                </div>
              </div>

              <div className="mt-5">
                <div className="text-xs font-semibold tracking-widest uppercase text-zinc-500 mb-2 flex items-center gap-2"><Key size={14} /> Clé API</div>
                <div className="flex items-center gap-2 bg-black/40 border border-white/10 rounded-xl px-4 py-3 font-mono text-sm">
                  <span className="flex-1 truncate text-cyan-300" data-testid="api-key-value">{reveal[s.entity_id] ? s.api_key : s.api_key.slice(0, 12) + "•".repeat(20)}</span>
                  <button onClick={() => setReveal((r) => ({ ...r, [s.entity_id]: !r[s.entity_id] }))} className="text-zinc-400 hover:text-white" data-testid="toggle-reveal">{reveal[s.entity_id] ? <EyeSlash size={18} /> : <Eye size={18} />}</button>
                  <button onClick={() => copy(s.api_key, "Clé API copiée")} className="text-zinc-400 hover:text-white" data-testid="copy-api-key"><Copy size={18} /></button>
                </div>
                <div className="flex items-center justify-between mt-2">
                  <span className="text-xs text-zinc-600">entity_id : <span className="font-mono">{s.entity_id}</span></span>
                  <button onClick={() => rotate(s.entity_id)} className="text-xs text-red-400 hover:text-red-300 flex items-center gap-1" data-testid="rotate-key"><ArrowsClockwise size={13} /> Régénérer</button>
                </div>
              </div>

              <div className="mt-4">
                <div className="text-xs font-semibold tracking-widest uppercase text-zinc-500 mb-2">Base URL</div>
                <div className="flex items-center gap-2 bg-black/40 border border-white/10 rounded-xl px-4 py-3 font-mono text-sm">
                  <span className="flex-1 truncate">{base}</span>
                  <button onClick={() => copy(base, "URL copiée")} className="text-zinc-400 hover:text-white"><Copy size={18} /></button>
                </div>
              </div>
            </div>

            {/* Endpoints */}
            <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6">
              <h3 className="font-display text-lg font-bold flex items-center gap-2 mb-4"><Code size={20} className="text-violet-400" /> Endpoints</h3>
              <div className="space-y-2 text-sm font-mono">
                {[
                  ["GET", "/v1/entity/me", "Infos & solde de l'entité"],
                  ["GET", "/v1/entity/balance", "Solde en CC et € "],
                  ["GET", "/v1/entity/transactions", "Historique des flux"],
                  ["GET", "/v1/frek/{frek_id}", "Résoudre une identité FREK-ID"],
                  ["POST", "/v1/entity/transfer", "Envoyer des CC (user ou entité)"],
                  ["POST", "/v1/entity/charge", "Encaisser un utilisateur"],
                ].map(([m, p, d]) => (
                  <div key={p} className="flex items-center gap-3 py-2 border-b border-white/5">
                    <span className={`text-xs font-bold px-2 py-0.5 rounded ${m === "GET" ? "bg-emerald-500/15 text-emerald-400" : "bg-violet-500/15 text-violet-300"}`}>{m}</span>
                    <span className="text-cyan-300 truncate">{p}</span>
                    <span className="text-zinc-500 text-xs ml-auto hidden sm:block font-sans">{d}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Curl examples */}
            <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6">
              <h3 className="font-display text-lg font-bold flex items-center gap-2 mb-4"><Terminal size={20} className="text-cyan-400" /> Exemples</h3>
              {[["Consulter le solde", curlBalance], ["Transférer des CC", curlTransfer]].map(([t, c]) => (
                <div key={t} className="mb-4">
                  <div className="text-xs text-zinc-500 mb-1">{t}</div>
                  <div className="relative bg-black/50 border border-white/10 rounded-xl p-4 pr-10">
                    <pre className="text-xs text-zinc-300 whitespace-pre-wrap break-all font-mono">{c}</pre>
                    <button onClick={() => copy(c, "Commande copiée")} className="absolute top-3 right-3 text-zinc-400 hover:text-white"><Copy size={16} /></button>
                  </div>
                </div>
              ))}
              <p className="text-xs text-zinc-600">Authentification par header <span className="font-mono text-zinc-400">X-API-Key</span>. Tous les flux inter-wallets passent par CVLN Fintech. Devise interne : Jeton CC (1 JCC = 1,50 €).</p>
            </div>
          </motion.div>
        )}
      </div>
    </div>
  );
}
