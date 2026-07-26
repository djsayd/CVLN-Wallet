import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { PaperPlaneTilt, Plus, WifiHigh, Bank, ArrowLineDown } from "@phosphor-icons/react";
import api, { fmt, fmtEur } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const CURRENCIES = ["eur", "usd", "gbp", "cad", "chf"];

export default function Wallet() {
  const { user, checkAuth } = useAuth();
  const [wallet, setWallet] = useState(null);
  const [recipient, setRecipient] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [depAmt, setDepAmt] = useState("");
  const [cur, setCur] = useState("eur");
  const [wdAmt, setWdAmt] = useState("");
  const [iban, setIban] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => setWallet((await api.get("/wallet")).data);
  useEffect(() => { load(); }, []);

  const send = async () => {
    if (!recipient || !amount) return toast.error("Renseignez le destinataire et le montant");
    setLoading(true);
    try {
      await api.post("/actions/send", { recipient, amount: parseFloat(amount), note });
      toast.success("Transfert effectué", { description: `${fmt(parseFloat(amount))} CC → ${recipient}` });
      setRecipient(""); setAmount(""); setNote(""); await load(); await checkAuth();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); } finally { setLoading(false); }
  };

  const deposit = async () => {
    if (!depAmt) return toast.error("Entrez un montant");
    setLoading(true);
    try {
      const res = await api.post("/payments/checkout", { amount: parseFloat(depAmt), currency: cur, origin_url: window.location.origin });
      window.location.href = res.data.checkout_url;
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); setLoading(false); }
  };

  const withdraw = async () => {
    if (!wdAmt) return toast.error("Entrez un montant en CC");
    setLoading(true);
    try {
      const res = await api.post("/withdrawals", { amount_cc: parseFloat(wdAmt), iban });
      toast.success("Demande de retrait enregistrée", { description: `${fmtEur(res.data.withdrawal.amount_eur)} — en attente de validation` });
      setWdAmt(""); setIban(""); await load(); await checkAuth();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); } finally { setLoading(false); }
  };

  const inputCls = "w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-violet-500 text-white placeholder:text-zinc-500 transition";

  return (
    <div className="space-y-8">
      <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Wallet</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <motion.div whileHover={{ y: -4 }} className="relative overflow-hidden rounded-3xl p-7 h-56 flex flex-col justify-between bg-gradient-to-br from-violet-700 via-violet-600 to-cyan-600 border border-white/10" data-testid="virtual-card">
          <div className="absolute -right-8 -bottom-8 w-40 h-40 rounded-full bg-white/10 blur-2xl" />
          <div className="flex items-center justify-between"><span className="font-display font-extrabold text-lg">CVLN</span><WifiHigh size={26} className="rotate-90" /></div>
          <div><div className="text-xs text-white/70 uppercase tracking-widest">Solde</div><div className="font-display text-3xl font-black tracking-tight">{fmt(wallet?.balance_cc)} CC</div></div>
          <div className="flex items-center justify-between text-sm"><span className="font-mono">{user?.frek_id}</span><span>{fmtEur(wallet?.value_eur)}</span></div>
        </motion.div>

        {/* Deposit via Stripe */}
        <div className="rounded-3xl border border-white/10 bg-[#12121A] p-7">
          <h2 className="font-display text-xl font-bold flex items-center gap-2 mb-1"><Plus size={20} className="text-cyan-400" /> Déposer (Stripe)</h2>
          <p className="text-xs text-zinc-500 mb-4">Paiement carte sécurisé · multi-devises</p>
          <div className="space-y-3">
            <div className="flex gap-2">
              <input className={inputCls} type="number" placeholder="Montant" value={depAmt} onChange={(e) => setDepAmt(e.target.value)} data-testid="deposit-amount" />
              <select className="bg-white/5 border border-white/10 rounded-xl px-3 outline-none uppercase text-sm" value={cur} onChange={(e) => setCur(e.target.value)} data-testid="deposit-currency">
                {CURRENCIES.map((c) => <option key={c} value={c} className="bg-[#12121A]">{c.toUpperCase()}</option>)}
              </select>
            </div>
            {depAmt > 0 && <div className="text-sm text-zinc-400">≈ <span className="text-cyan-400 font-semibold">{fmt(depAmt / (wallet?.rate || 1.5), 2)} CC</span></div>}
            <button onClick={deposit} disabled={loading} className="w-full py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold glow-violet active:scale-95 transition-transform disabled:opacity-50" data-testid="deposit-submit">Payer avec Stripe</button>
          </div>
        </div>

        {/* Send CC */}
        <div className="rounded-3xl border border-white/10 bg-[#12121A] p-7">
          <h2 className="font-display text-xl font-bold flex items-center gap-2 mb-4"><PaperPlaneTilt size={20} className="text-violet-400" /> Envoyer des CC</h2>
          <div className="space-y-3">
            <input className={inputCls} placeholder="Destinataire (FREK-ID)" value={recipient} onChange={(e) => setRecipient(e.target.value)} data-testid="send-recipient" />
            <input className={inputCls} type="number" placeholder="Montant (CC)" value={amount} onChange={(e) => setAmount(e.target.value)} data-testid="send-amount" />
            <input className={inputCls} placeholder="Note (optionnel)" value={note} onChange={(e) => setNote(e.target.value)} data-testid="send-note" />
            <button onClick={send} disabled={loading} className="w-full py-3 rounded-full bg-white/10 border border-white/10 font-semibold hover:bg-white/15 active:scale-95 transition-transform disabled:opacity-50" data-testid="send-submit">Envoyer</button>
          </div>
        </div>
      </div>

      {/* Withdraw */}
      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-7 max-w-xl">
        <h2 className="font-display text-xl font-bold flex items-center gap-2 mb-1"><Bank size={20} className="text-emerald-400" /> Retirer vers compte bancaire</h2>
        <p className="text-xs text-zinc-500 mb-4">Virement traité via Stripe · soumis à validation admin (KYC en live)</p>
        <div className="space-y-3">
          <input className={inputCls} type="number" placeholder="Montant (CC)" value={wdAmt} onChange={(e) => setWdAmt(e.target.value)} data-testid="withdraw-amount" />
          {wdAmt > 0 && <div className="text-sm text-zinc-400">≈ <span className="text-emerald-400 font-semibold">{fmtEur(wdAmt * (wallet?.rate || 1.5))}</span></div>}
          <input className={inputCls} placeholder="IBAN (bénéficiaire)" value={iban} onChange={(e) => setIban(e.target.value)} data-testid="withdraw-iban" />
          <button onClick={withdraw} disabled={loading} className="w-full py-3 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/20 font-semibold hover:bg-emerald-500/20 active:scale-95 transition-transform disabled:opacity-50" data-testid="withdraw-submit">Demander le retrait</button>
        </div>
      </div>
    </div>
  );
}
