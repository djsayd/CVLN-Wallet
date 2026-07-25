import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { PaperPlaneTilt, ArrowLineDown, MagnifyingGlass } from "@phosphor-icons/react";
import api, { fmt, relDate } from "@/lib/api";

export default function Transactions() {
  const [txs, setTxs] = useState([]);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");

  useEffect(() => { (async () => setTxs((await api.get("/transactions?limit=200")).data))(); }, []);

  const filtered = txs.filter((t) => {
    if (filter === "in" && t.amount <= 0) return false;
    if (filter === "out" && t.amount >= 0) return false;
    if (q && !t.label.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  const inputCls = "bg-white/5 border border-white/10 rounded-full px-4 py-2.5 outline-none focus:ring-2 focus:ring-violet-500 text-white placeholder:text-zinc-500 text-sm";

  return (
    <div className="space-y-6">
      <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Transactions</h1>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-full px-4 py-2.5 flex-1 min-w-[220px]">
          <MagnifyingGlass size={18} className="text-zinc-500" />
          <input className="bg-transparent outline-none text-sm text-white placeholder:text-zinc-500 w-full" placeholder="Rechercher..." value={q} onChange={(e) => setQ(e.target.value)} data-testid="tx-search" />
        </div>
        {[["all", "Tout"], ["in", "Entrées"], ["out", "Sorties"]].map(([k, l]) => (
          <button key={k} onClick={() => setFilter(k)} data-testid={`filter-${k}`}
            className={`px-4 py-2.5 rounded-full text-sm font-semibold transition-colors ${filter === k ? "bg-violet-600 text-white" : "bg-white/5 text-zinc-400 hover:text-white"}`}>
            {l}
          </button>
        ))}
      </div>

      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-4 sm:p-6">
        <div className="divide-y divide-white/5">
          {filtered.map((t, i) => (
            <motion.div key={t.tx_id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: Math.min(i * 0.02, 0.4) }}
              className="flex items-center gap-4 py-4 px-2 hover:bg-white/5 rounded-xl transition-colors" data-testid={`tx-item-${i}`}>
              <div className={`w-11 h-11 rounded-xl flex items-center justify-center ${t.amount > 0 ? "bg-emerald-500/10 text-emerald-400" : "bg-white/5 text-zinc-400"}`}>
                {t.amount > 0 ? <ArrowLineDown size={18} weight="bold" /> : <PaperPlaneTilt size={18} weight="bold" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-semibold truncate">{t.label}</div>
                <div className="text-xs text-zinc-500">{relDate(t.created_at)} · {t.category}</div>
              </div>
              <div className={`font-semibold tabular-nums ${t.amount > 0 ? "text-emerald-400" : "text-white"}`}>
                {t.amount > 0 ? "+" : ""}{fmt(t.amount)} CC
              </div>
            </motion.div>
          ))}
          {filtered.length === 0 && <div className="py-16 text-center text-zinc-500">Aucune transaction</div>}
        </div>
      </div>
    </div>
  );
}
