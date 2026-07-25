import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { toast } from "sonner";
import {
  TrendUp, PaperPlaneTilt, ArrowLineDown, ArrowsLeftRight, QrCode,
  CreditCard, Plus, Coins, ChartLineUp, SealCheck,
} from "@phosphor-icons/react";
import api, { fmt, fmtEur, relDate } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const ACTIONS = [
  { key: "send", label: "Envoyer", icon: PaperPlaneTilt },
  { key: "receive", label: "Recevoir", icon: ArrowLineDown },
  { key: "convert", label: "Convertir", icon: ArrowsLeftRight },
  { key: "qr", label: "Scanner QR", icon: QrCode },
  { key: "pay", label: "Payer", icon: CreditCard },
  { key: "buy", label: "Acheter CC", icon: Plus },
];

export default function Dashboard() {
  const { user, checkAuth } = useAuth();
  const navigate = useNavigate();
  const [wallet, setWallet] = useState(null);
  const [txs, setTxs] = useState([]);
  const [coffres, setCoffres] = useState([]);

  const load = async () => {
    const [w, t, c] = await Promise.all([
      api.get("/wallet"), api.get("/transactions?limit=6"), api.get("/coffres"),
    ]);
    setWallet(w.data); setTxs(t.data); setCoffres(c.data);
  };
  useEffect(() => { load(); }, []);

  const onAction = (key) => {
    if (key === "convert") return navigate("/convertir");
    if (key === "buy") return navigate("/convertir");
    if (key === "send") return navigate("/wallet");
    if (key === "pay") return navigate("/marketplace");
    toast.info("Fonction bientôt disponible", { description: "Cette action sera activée prochainement." });
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">
          Bonjour {user?.name?.split(" ")[0]} 👋
        </h1>
        <p className="text-zinc-400 mt-1">Bienvenue dans votre espace financier CVLN.</p>
      </div>

      {/* Bento cards */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
        <motion.div
          whileHover={{ y: -4 }}
          className="lg:col-span-2 relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-violet-600/25 via-[#12121A] to-[#12121A] p-8"
          data-testid="total-balance-card"
        >
          <div className="absolute -right-10 -top-10 w-48 h-48 rounded-full bg-violet-500/20 blur-3xl" />
          <div className="flex items-center gap-2 text-xs font-semibold tracking-[0.2em] uppercase text-zinc-400">
            <Coins size={16} className="text-violet-400" /> Solde Total
          </div>
          <div className="font-display text-5xl sm:text-6xl font-black tracking-tighter mt-4" data-testid="total-balance">
            {fmt(wallet?.balance_cc)} <span className="text-2xl text-violet-400 font-bold">CC</span>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <span className="inline-flex items-center gap-1 text-emerald-400 font-semibold text-sm bg-emerald-500/10 px-3 py-1 rounded-full">
              <TrendUp size={16} weight="bold" /> +{wallet?.change_pct ?? "—"} %
            </span>
            <span className="text-zinc-500 text-sm">ce mois</span>
          </div>
        </motion.div>

        <motion.div whileHover={{ y: -4 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-7" data-testid="value-eur-card">
          <div className="flex items-center gap-2 text-xs font-semibold tracking-[0.2em] uppercase text-zinc-400">
            <ChartLineUp size={16} className="text-cyan-400" /> Valeur estimée
          </div>
          <div className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight mt-4">{fmtEur(wallet?.value_eur)}</div>
          <div className="text-sm text-zinc-500 mt-3">≈ selon marché · 1 JCC = 1,50 €</div>
        </motion.div>

        <motion.div whileHover={{ y: -4 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-7 relative overflow-hidden" data-testid="frek-score-card">
          <div className="flex items-center gap-2 text-xs font-semibold tracking-[0.2em] uppercase text-zinc-400">
            <SealCheck size={16} className="text-violet-400" /> FREK Score
          </div>
          <div className="flex items-end gap-4 mt-4">
            <div className="font-display text-5xl font-black tracking-tighter text-violet-400">{wallet?.frek_score ?? "—"}</div>
            <div className="mb-1 flex-1">
              <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                <div className="h-full bg-gradient-to-r from-violet-500 to-cyan-400" style={{ width: `${((wallet?.frek_score || 0) / 1000) * 100}%` }} />
              </div>
            </div>
          </div>
          <div className="text-sm text-zinc-500 mt-3">Niveau {wallet?.frek_level}</div>
        </motion.div>
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
        {ACTIONS.map((a, i) => (
          <motion.button
            key={a.key}
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
            onClick={() => onAction(a.key)}
            data-testid={`action-${a.key}`}
            className="flex flex-col items-center justify-center gap-2 p-4 rounded-2xl bg-white/5 border border-white/5 hover:border-violet-500/40 hover:bg-white/10 active:scale-95 transition-transform"
          >
            <span className="w-10 h-10 rounded-xl bg-violet-500/15 text-violet-300 flex items-center justify-center">
              <a.icon size={20} weight="bold" />
            </span>
            <span className="text-xs font-semibold text-zinc-300">{a.label}</span>
          </motion.button>
        ))}
      </div>

      {/* Grid: transactions + coffres */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2 rounded-3xl border border-white/10 bg-[#12121A] p-6 sm:p-7">
          <div className="flex items-center justify-between mb-5">
            <h2 className="font-display text-xl font-bold">Dernières transactions</h2>
            <button onClick={() => navigate("/transactions")} className="text-sm text-violet-400 hover:text-violet-300 font-semibold" data-testid="see-all-tx">Tout voir</button>
          </div>
          <div className="divide-y divide-white/5">
            {txs.map((t, i) => (
              <motion.div
                key={t.tx_id}
                initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
                className="flex items-center gap-4 py-3.5"
                data-testid={`tx-row-${i}`}
              >
                <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${t.amount > 0 ? "bg-emerald-500/10 text-emerald-400" : "bg-white/5 text-zinc-400"}`}>
                  {t.amount > 0 ? <ArrowLineDown size={18} weight="bold" /> : <PaperPlaneTilt size={18} weight="bold" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="font-semibold truncate">{t.label}</div>
                  <div className="text-xs text-zinc-500">{relDate(t.created_at)} · {t.category}</div>
                </div>
                <div className={`font-semibold tabular-nums ${t.amount > 0 ? "text-emerald-400" : "text-white"}`}>
                  {t.amount > 0 ? "+" : ""}{fmt(t.amount)} CC
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          {coffres.map((c) => {
            const pct = Math.min(100, Math.round((c.amount_cc / c.goal_cc) * 100));
            return (
              <div key={c.coffre_id} className="rounded-2xl border border-white/10 bg-[#12121A] p-5 card-hover" data-testid={`coffre-mini-${c.coffre_id}`}>
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">{c.name}</h3>
                  <span className="text-xs text-zinc-500">{pct}%</span>
                </div>
                <div className="text-sm text-zinc-400 mt-1">{fmt(c.amount_cc)} CC</div>
                <div className="h-2 rounded-full bg-white/10 mt-3 overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${c.color}, #00F0FF)` }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
