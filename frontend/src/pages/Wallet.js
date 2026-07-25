import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { PaperPlaneTilt, Plus, Copy, WifiHigh } from "@phosphor-icons/react";
import api, { fmt, fmtEur } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function Wallet() {
  const { user, checkAuth } = useAuth();
  const [wallet, setWallet] = useState(null);
  const [recipient, setRecipient] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [buyEur, setBuyEur] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => setWallet((await api.get("/wallet")).data);
  useEffect(() => { load(); }, []);

  const send = async () => {
    if (!recipient || !amount) return toast.error("Renseignez le destinataire et le montant");
    setLoading(true);
    try {
      await api.post("/actions/send", { recipient, amount: parseFloat(amount), note });
      toast.success("Transfert effectué", { description: `${fmt(parseFloat(amount))} CC envoyés à ${recipient}` });
      setRecipient(""); setAmount(""); setNote("");
      await load(); await checkAuth();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
    finally { setLoading(false); }
  };

  const buy = async () => {
    if (!buyEur) return toast.error("Entrez un montant en €");
    setLoading(true);
    try {
      const res = await api.post("/actions/buy", { amount_eur: parseFloat(buyEur) });
      toast.success("Achat réussi", { description: `+${fmt(res.data.cc)} CC crédités` });
      setBuyEur(""); await load(); await checkAuth();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
    finally { setLoading(false); }
  };

  const inputCls = "w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent text-white placeholder:text-zinc-500 transition";

  return (
    <div className="space-y-8">
      <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Wallet</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Virtual card */}
        <motion.div whileHover={{ y: -4 }} className="lg:col-span-1 relative overflow-hidden rounded-3xl p-7 h-56 flex flex-col justify-between bg-gradient-to-br from-violet-700 via-violet-600 to-cyan-600 border border-white/10" data-testid="virtual-card">
          <div className="absolute -right-8 -bottom-8 w-40 h-40 rounded-full bg-white/10 blur-2xl" />
          <div className="flex items-center justify-between">
            <span className="font-display font-extrabold text-lg">CVLN</span>
            <WifiHigh size={26} className="rotate-90" />
          </div>
          <div>
            <div className="text-xs text-white/70 uppercase tracking-widest">Solde</div>
            <div className="font-display text-3xl font-black tracking-tight">{fmt(wallet?.balance_cc)} CC</div>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="font-mono">{user?.frek_id}</span>
            <span>{fmtEur(wallet?.value_eur)}</span>
          </div>
        </motion.div>

        {/* Send */}
        <div className="lg:col-span-1 rounded-3xl border border-white/10 bg-[#12121A] p-7">
          <h2 className="font-display text-xl font-bold flex items-center gap-2 mb-5"><PaperPlaneTilt size={20} className="text-violet-400" /> Envoyer des CC</h2>
          <div className="space-y-3">
            <input className={inputCls} placeholder="Destinataire (FREK-ID / nom)" value={recipient} onChange={(e) => setRecipient(e.target.value)} data-testid="send-recipient" />
            <input className={inputCls} type="number" placeholder="Montant (CC)" value={amount} onChange={(e) => setAmount(e.target.value)} data-testid="send-amount" />
            <input className={inputCls} placeholder="Note (optionnel)" value={note} onChange={(e) => setNote(e.target.value)} data-testid="send-note" />
            <button onClick={send} disabled={loading} className="w-full py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold glow-violet hover:from-violet-500 active:scale-95 transition-transform disabled:opacity-50" data-testid="send-submit">
              Envoyer
            </button>
          </div>
        </div>

        {/* Buy */}
        <div className="lg:col-span-1 rounded-3xl border border-white/10 bg-[#12121A] p-7">
          <h2 className="font-display text-xl font-bold flex items-center gap-2 mb-5"><Plus size={20} className="text-cyan-400" /> Acheter des CC</h2>
          <div className="space-y-3">
            <input className={inputCls} type="number" placeholder="Montant en €" value={buyEur} onChange={(e) => setBuyEur(e.target.value)} data-testid="buy-eur" />
            {buyEur > 0 && <div className="text-sm text-zinc-400">≈ <span className="text-cyan-400 font-semibold">{fmt(buyEur / 1.5, 2)} CC</span> (1 JCC = 1,50 €)</div>}
            <button onClick={buy} disabled={loading} className="w-full py-3 rounded-full bg-white/10 border border-white/10 font-semibold hover:bg-white/15 active:scale-95 transition-transform disabled:opacity-50" data-testid="buy-submit">
              Acheter
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
