import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { WifiHigh, Snowflake, Lock, LockOpen, DeviceMobile, ShoppingCart, Storefront } from "@phosphor-icons/react";
import api, { fmt, relDate } from "@/lib/api";

function platform() {
  const ua = navigator.userAgent || "";
  if (/iPhone|iPad|iPod|Macintosh/.test(ua)) return "apple";
  if (/Android/.test(ua)) return "google";
  return "other";
}

export default function Card() {
  const [card, setCard] = useState(null);
  const [txs, setTxs] = useState([]);
  const [elig, setElig] = useState(null);
  const plat = platform();

  const load = async () => {
    const [c, t] = await Promise.all([api.get("/card"), api.get("/card/transactions")]);
    setCard(c.data); setTxs(t.data);
  };
  useEffect(() => { load(); }, []);

  const toggleFreeze = async () => {
    try {
      const frozen = card.status === "frozen";
      await api.post(`/card/${frozen ? "unfreeze" : "freeze"}`);
      toast.success(frozen ? "Carte réactivée" : "Carte gelée");
      await load();
    } catch { toast.error("Erreur"); }
  };

  const setLimit = async (field, value) => {
    try { const r = await api.put("/card/limits", { [field]: parseFloat(value) }); setCard({ ...card, ...r.data }); toast.success("Plafond mis à jour"); }
    catch { toast.error("Erreur"); }
  };

  const checkWallet = async () => {
    try {
      const r = await api.get("/card/wallet-eligibility"); setElig(r.data);
      const p = r.data[plat === "apple" ? "apple" : "google"];
      toast.info(`Statut : ${p?.status}`, { description: p?.reason });
    } catch { toast.error("Erreur"); }
  };

  if (!card) return <div className="text-zinc-500">Chargement…</div>;
  const frozen = card.status === "frozen";
  const dailyPct = Math.min(100, Math.round((card.today_spent_cc / card.daily_limit_cc) * 100));

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Carte CVLN</h1>
        <p className="text-zinc-400 mt-1">Carte virtuelle · <span className="text-amber-400">émission MOCK</span> (aucun issuer réel connecté)</p>
      </div>

      {/* Card visual */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
        className={`relative overflow-hidden rounded-3xl p-7 h-56 flex flex-col justify-between border border-white/10 transition-all ${frozen ? "bg-gradient-to-br from-zinc-700 to-zinc-900 grayscale" : "bg-gradient-to-br from-violet-700 via-violet-600 to-cyan-600"}`} data-testid="card-visual">
        <div className="absolute -right-8 -bottom-8 w-40 h-40 rounded-full bg-white/10 blur-2xl" />
        <div className="flex items-center justify-between">
          <span className="font-display font-extrabold text-lg">CVLN</span>
          {frozen ? <Snowflake size={26} weight="fill" /> : <WifiHigh size={26} className="rotate-90" />}
        </div>
        <div className="font-mono text-xl tracking-widest">•••• •••• •••• {card.last4}</div>
        <div className="flex items-center justify-between text-sm">
          <span>EXP {String(card.exp_month).padStart(2, "0")}/{String(card.exp_year).slice(2)}</span>
          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${frozen ? "bg-black/40" : "bg-white/20"}`}>{frozen ? "GELÉE" : "ACTIVE"}</span>
        </div>
      </motion.div>

      {/* Controls */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <button onClick={toggleFreeze} data-testid="freeze-toggle" className={`rounded-2xl border border-white/10 p-5 text-left card-hover ${frozen ? "bg-cyan-500/10" : "bg-[#12121A]"}`}>
          {frozen ? <LockOpen size={22} className="text-cyan-400" /> : <Lock size={22} className="text-violet-400" />}
          <div className="font-semibold mt-2">{frozen ? "Réactiver" : "Geler la carte"}</div>
          <div className="text-xs text-zinc-500">{frozen ? "Reprendre les paiements" : "Bloquer tous les paiements"}</div>
        </button>
        <div className="rounded-2xl border border-white/10 bg-[#12121A] p-5">
          <div className="text-xs text-zinc-500">Dépenses aujourd'hui</div>
          <div className="font-display text-xl font-black mt-1">{fmt(card.today_spent_cc)} / {fmt(card.daily_limit_cc)} CC</div>
          <div className="h-2 rounded-full bg-white/10 mt-3 overflow-hidden"><div className="h-full bg-gradient-to-r from-violet-500 to-cyan-400" style={{ width: `${dailyPct}%` }} /></div>
        </div>
        <div className="rounded-2xl border border-white/10 bg-[#12121A] p-5 space-y-2">
          <label className="text-xs text-zinc-500">Plafond / transaction (CC)</label>
          <input type="number" defaultValue={card.per_tx_limit_cc} onBlur={(e) => setLimit("per_tx_limit_cc", e.target.value)} data-testid="per-tx-limit" className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-violet-500" />
          <label className="text-xs text-zinc-500">Plafond quotidien (CC)</label>
          <input type="number" defaultValue={card.daily_limit_cc} onBlur={(e) => setLimit("daily_limit_cc", e.target.value)} data-testid="daily-limit" className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-violet-500" />
        </div>
      </div>

      {/* Mobile wallet — honest smart button */}
      <div className="rounded-2xl border border-white/10 bg-[#12121A] p-6">
        <h2 className="font-display text-lg font-bold flex items-center gap-2"><DeviceMobile size={20} className="text-violet-400" /> Wallet mobile</h2>
        <div className="mt-4">
          {plat === "other" ? (
            <div className="text-sm text-zinc-500">Wallet mobile indisponible sur cet appareil (ouvrez depuis iOS ou Android).</div>
          ) : (
            <>
              <button onClick={checkWallet} data-testid="add-to-wallet" className="px-5 py-3 rounded-full bg-white text-black font-semibold active:scale-95 transition-transform">
                {plat === "apple" ? "Ajouter à Apple Wallet" : "Ajouter à Google Wallet"}
              </button>
              <p className="text-xs text-zinc-500 mt-3">
                {elig ? `Statut : ${elig[plat].status} — ${elig[plat].reason}` : "Vérifiez l'éligibilité (statut actuel : PLANNED — issuer requis)."}
              </p>
            </>
          )}
        </div>
      </div>

      {/* Card transactions */}
      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6">
        <h2 className="font-display text-lg font-bold mb-4">Paiements carte & agent</h2>
        <div className="divide-y divide-white/5">
          {txs.map((t, i) => (
            <div key={t.tx_id} className="flex items-center gap-3 py-3" data-testid={`card-tx-${i}`}>
              <span className="w-9 h-9 rounded-lg bg-white/5 flex items-center justify-center text-zinc-300">{t.category === "Agent" ? <ShoppingCart size={16} /> : <Storefront size={16} />}</span>
              <div className="flex-1 min-w-0"><div className="font-semibold truncate text-sm">{t.label}</div><div className="text-xs text-zinc-500">{relDate(t.created_at)} · {t.category}</div></div>
              <div className={`font-semibold text-sm ${t.amount > 0 ? "text-emerald-400" : "text-white"}`}>{t.amount > 0 ? "+" : ""}{fmt(t.amount)} CC</div>
            </div>
          ))}
          {txs.length === 0 && <div className="py-8 text-center text-zinc-500">Aucun paiement carte</div>}
        </div>
      </div>
    </div>
  );
}
