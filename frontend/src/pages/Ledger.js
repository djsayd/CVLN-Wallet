import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Scales, CheckCircle, WarningCircle, Stack } from "@phosphor-icons/react";
import api, { fmt } from "@/lib/api";

export default function Ledger() {
  const [integrity, setIntegrity] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [entries, setEntries] = useState([]);

  useEffect(() => {
    (async () => {
      const [i, a, e] = await Promise.all([
        api.get("/admin/ledger/integrity"), api.get("/ledger/accounts"), api.get("/ledger/entries?limit=40")]);
      setIntegrity(i.data); setAccounts(a.data); setEntries(e.data);
    })();
  }, []);

  const balanced = integrity?.balanced;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Grand Livre</h1>
        <p className="text-zinc-400 mt-1">Financial Core · comptabilité en partie double · source unique de vérité.</p>
      </div>

      {/* Integrity banner */}
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className={`rounded-3xl border p-6 flex items-center gap-4 ${balanced ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5"}`} data-testid="integrity-banner">
        {balanced ? <CheckCircle size={40} weight="fill" className="text-emerald-400" /> : <WarningCircle size={40} weight="fill" className="text-red-400" />}
        <div>
          <div className="font-display text-xl font-bold">{balanced ? "Ledger équilibré" : "Déséquilibre détecté"}</div>
          <div className="text-sm text-zinc-400">
            {integrity?.entries ?? "—"} écritures · Σ par asset : {integrity ? JSON.stringify(integrity.per_asset_sum) : "…"} · divergences cache : {integrity?.cache_mismatches?.length ?? 0}
          </div>
        </div>
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* System accounts */}
        <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6">
          <h2 className="font-display text-lg font-bold mb-4 flex items-center gap-2"><Stack size={20} className="text-violet-400" /> Comptes système</h2>
          <div className="divide-y divide-white/5">
            {integrity && Object.entries(integrity.system_accounts).map(([k, v]) => (
              <div key={k} className="flex justify-between py-2.5 text-sm" data-testid={`sys-${k}`}>
                <span className="text-zinc-400 font-mono">acct_sys_{k}</span>
                <span className="font-semibold tabular-nums">{fmt(v)} CC</span>
              </div>
            ))}
          </div>
        </div>

        {/* My accounts (derived vs cache) */}
        <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6">
          <h2 className="font-display text-lg font-bold mb-4 flex items-center gap-2"><Scales size={20} className="text-cyan-400" /> Comptes (dérivé vs cache)</h2>
          <div className="divide-y divide-white/5">
            {accounts.map((a) => (
              <div key={a.account_id} className="flex justify-between py-2.5 text-sm" data-testid={`acct-${a.account_id}`}>
                <span className="text-zinc-300">{a.name}</span>
                <span className="font-semibold tabular-nums">{fmt(a.balance)} CC {Math.abs(a.balance - a.cached) < 0.01 ? <span className="text-emerald-400">✓</span> : <span className="text-red-400">≠{fmt(a.cached)}</span>}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Journal */}
      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6">
        <h2 className="font-display text-lg font-bold mb-4">Journal</h2>
        <div className="divide-y divide-white/5">
          {entries.map((e) => (
            <div key={e.entry_id} className="py-3" data-testid={`entry-${e.entry_id}`}>
              <div className="flex items-center justify-between">
                <span className="font-semibold text-sm">{e.description}</span>
                <span className="text-xs text-zinc-500">{e.category}</span>
              </div>
              <div className="flex flex-wrap gap-2 mt-1.5">
                {e.postings.map((p, i) => (
                  <span key={i} className={`text-[11px] font-mono px-2 py-0.5 rounded ${p.amount >= 0 ? "bg-emerald-500/10 text-emerald-300" : "bg-red-500/10 text-red-300"}`}>
                    {p.account_id.replace("acct_", "")}: {p.amount >= 0 ? "+" : ""}{fmt(p.amount)}
                  </span>
                ))}
              </div>
            </div>
          ))}
          {entries.length === 0 && <div className="py-8 text-center text-zinc-500">Aucune écriture</div>}
        </div>
      </div>
    </div>
  );
}
