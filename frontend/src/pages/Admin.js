import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { Users, Coins, Bank, Gauge, Check, X } from "@phosphor-icons/react";
import api, { fmt, fmtEur } from "@/lib/api";

export default function Admin() {
  const [stats, setStats] = useState(null);
  const [settings, setSettings] = useState(null);
  const [wds, setWds] = useState([]);
  const [rate, setRate] = useState("");
  const [minDep, setMinDep] = useState("");
  const [reserve, setReserve] = useState("");

  const load = async () => {
    const [s, se, w] = await Promise.all([api.get("/admin/stats"), api.get("/admin/settings"), api.get("/admin/withdrawals")]);
    setStats(s.data); setSettings(se.data); setWds(w.data);
    setRate(se.data.rate_eur); setMinDep(se.data.min_deposit_eur); setReserve(se.data.reserve_cc);
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    try {
      await api.put("/admin/settings", { rate_eur: parseFloat(rate), min_deposit_eur: parseFloat(minDep), reserve_cc: parseFloat(reserve) });
      toast.success("Paramètres mis à jour"); await load();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
  };

  const act = async (id, action) => {
    try { await api.post(`/admin/withdrawals/${id}/${action}`); toast.success(action === "approve" ? "Retrait validé" : "Retrait refusé (remboursé)"); await load(); }
    catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
  };

  const inputCls = "w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-violet-500 text-white";
  const cards = [
    { icon: Users, label: "Utilisateurs", value: stats?.users, color: "text-violet-400" },
    { icon: Coins, label: "CC en circulation", value: `${fmt(stats?.circulation_cc)} CC`, color: "text-cyan-400" },
    { icon: Gauge, label: "Valeur circulation", value: fmtEur(stats?.circulation_eur), color: "text-emerald-400" },
    { icon: Bank, label: "Retraits en attente", value: stats?.pending_withdrawals, color: "text-amber-400" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Back-office Admin</h1>
        <p className="text-zinc-400 mt-1">Pilotage de la valeur du CC, réserve et validation des retraits.</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((c) => (
          <motion.div key={c.label} whileHover={{ y: -4 }} className="rounded-2xl border border-white/10 bg-[#12121A] p-5" data-testid={`stat-${c.label}`}>
            <c.icon size={22} className={c.color} />
            <div className="font-display text-2xl font-black mt-3">{c.value ?? "—"}</div>
            <div className="text-xs text-zinc-500 mt-1">{c.label}</div>
          </motion.div>
        ))}
      </div>

      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-7 max-w-2xl">
        <h2 className="font-display text-lg font-bold mb-4">Paramètres monétaires</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div><label className="text-xs text-zinc-500">Taux 1 JCC = € </label><input className={inputCls} type="number" step="0.01" value={rate} onChange={(e) => setRate(e.target.value)} data-testid="admin-rate" /></div>
          <div><label className="text-xs text-zinc-500">Dépôt minimum (€)</label><input className={inputCls} type="number" value={minDep} onChange={(e) => setMinDep(e.target.value)} data-testid="admin-mindep" /></div>
          <div><label className="text-xs text-zinc-500">Réserve (CC)</label><input className={inputCls} type="number" value={reserve} onChange={(e) => setReserve(e.target.value)} data-testid="admin-reserve" /></div>
        </div>
        <button onClick={save} className="mt-5 px-6 py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold active:scale-95 transition-transform" data-testid="admin-save">Enregistrer</button>
      </div>

      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6 sm:p-7">
        <h2 className="font-display text-lg font-bold mb-4">Demandes de retrait</h2>
        <div className="divide-y divide-white/5">
          {wds.map((w) => (
            <div key={w.wd_id} className="flex items-center gap-4 py-4 flex-wrap" data-testid={`wd-${w.wd_id}`}>
              <div className="flex-1 min-w-0">
                <div className="font-semibold">{w.user_name} · <span className="font-mono text-violet-400 text-sm">{w.frek_id}</span></div>
                <div className="text-xs text-zinc-500">{fmt(w.amount_cc)} CC → {fmtEur(w.amount_eur)} · IBAN {w.iban || "—"}</div>
              </div>
              {w.status === "pending" ? (
                <div className="flex gap-2">
                  <button onClick={() => act(w.wd_id, "approve")} className="px-3 py-2 rounded-lg bg-emerald-500/15 text-emerald-300 text-sm font-semibold flex items-center gap-1" data-testid={`approve-${w.wd_id}`}><Check size={16} /> Valider</button>
                  <button onClick={() => act(w.wd_id, "reject")} className="px-3 py-2 rounded-lg bg-red-500/15 text-red-300 text-sm font-semibold flex items-center gap-1" data-testid={`reject-${w.wd_id}`}><X size={16} /> Refuser</button>
                </div>
              ) : (
                <span className={`text-xs font-semibold px-3 py-1.5 rounded-full ${w.status === "processed" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>{w.status === "processed" ? "Virement traité" : "Refusé"}</span>
              )}
            </div>
          ))}
          {wds.length === 0 && <div className="py-10 text-center text-zinc-500">Aucune demande</div>}
        </div>
      </div>
    </div>
  );
}
