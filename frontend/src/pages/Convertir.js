import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { ArrowsDownUp } from "@phosphor-icons/react";
import api, { fmt } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const RATE = 1.5;

export default function Convertir() {
  const { checkAuth } = useAuth();
  const [wallet, setWallet] = useState(null);
  const [dir, setDir] = useState("eur_to_jcc"); // or jcc_to_eur
  const [amount, setAmount] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => setWallet((await api.get("/wallet")).data);
  useEffect(() => { load(); }, []);

  const eurToJcc = dir === "eur_to_jcc";
  const amt = parseFloat(amount) || 0;
  const result = eurToJcc ? amt / RATE : amt * RATE;
  const fromLabel = eurToJcc ? "€" : "CC";
  const toLabel = eurToJcc ? "CC" : "€";

  const swap = () => { setDir(eurToJcc ? "jcc_to_eur" : "eur_to_jcc"); setAmount(""); };

  const convert = async () => {
    if (!amt) return toast.error("Entrez un montant");
    setLoading(true);
    try {
      const res = await api.post("/convert", { direction: dir, amount: amt });
      toast.success("Conversion réussie", { description: eurToJcc ? `+${fmt(res.data.received_cc, 2)} CC` : `+${fmt(res.data.received_eur, 2)} €` });
      setAmount(""); await load(); await checkAuth();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
    finally { setLoading(false); }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Convertir</h1>
        <p className="text-zinc-400 mt-1">Taux fixe · <span className="text-violet-400 font-semibold">1 JCC = 1,50 €</span></p>
      </div>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-7 sm:p-8">
        <div className="rounded-2xl bg-white/5 border border-white/10 p-5">
          <div className="text-xs font-semibold tracking-widest uppercase text-zinc-500 mb-2">Vous convertissez</div>
          <div className="flex items-center gap-3">
            <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0"
              className="bg-transparent outline-none font-display text-4xl font-black tracking-tight w-full text-white placeholder:text-zinc-600" data-testid="convert-amount" autoFocus />
            <span className="font-display text-2xl font-bold text-violet-400">{fromLabel}</span>
          </div>
          {!eurToJcc && <div className="text-xs text-zinc-500 mt-2">Disponible : {fmt(wallet?.balance_cc)} CC</div>}
        </div>

        <div className="flex justify-center -my-3 relative z-10">
          <button onClick={swap} data-testid="swap-direction" className="w-12 h-12 rounded-full bg-violet-600 hover:bg-violet-500 flex items-center justify-center glow-violet active:scale-90 transition-transform border-4 border-[#12121A]">
            <ArrowsDownUp size={22} weight="bold" />
          </button>
        </div>

        <div className="rounded-2xl bg-white/5 border border-white/10 p-5">
          <div className="text-xs font-semibold tracking-widest uppercase text-zinc-500 mb-2">Vous recevez</div>
          <div className="flex items-center gap-3">
            <div className="font-display text-4xl font-black tracking-tight w-full text-cyan-400" data-testid="convert-result">{fmt(result, 2)}</div>
            <span className="font-display text-2xl font-bold text-cyan-400">{toLabel}</span>
          </div>
        </div>

        <button onClick={convert} disabled={loading} className="w-full mt-6 py-3.5 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold glow-violet hover:from-violet-500 active:scale-95 transition-transform disabled:opacity-50" data-testid="convert-submit">
          Convertir
        </button>
      </motion.div>
    </div>
  );
}
