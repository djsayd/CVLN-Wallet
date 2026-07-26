import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { Plus, Bank, ArrowsLeftRight } from "@phosphor-icons/react";

export default function Convertir() {
  const navigate = useNavigate();
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Convertir</h1>
        <p className="text-zinc-400 mt-1">Toute opération en devise passe par <span className="text-violet-400 font-semibold">Stripe</span>. Taux : 1 JCC = 1,50 €.</p>
      </div>

      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-7 flex items-center gap-4">
        <ArrowsLeftRight size={28} className="text-violet-400" />
        <p className="text-sm text-zinc-300">Chaque CC est adossé à un vrai encaissement : impossible de créer de la monnaie. Les entrées se font par dépôt Stripe, les sorties par retrait bancaire validé.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <motion.button whileHover={{ y: -4 }} onClick={() => navigate("/wallet")} className="rounded-3xl border border-white/10 bg-gradient-to-br from-violet-600/20 to-[#12121A] p-7 text-left" data-testid="go-deposit">
          <span className="w-11 h-11 rounded-xl bg-cyan-500/15 text-cyan-300 flex items-center justify-center"><Plus size={22} weight="bold" /></span>
          <h3 className="font-display text-xl font-bold mt-4">Déposer (EUR → CC)</h3>
          <p className="text-sm text-zinc-400 mt-1">Paiement carte via Stripe, multi-devises.</p>
        </motion.button>
        <motion.button whileHover={{ y: -4 }} onClick={() => navigate("/wallet")} className="rounded-3xl border border-white/10 bg-gradient-to-br from-emerald-600/15 to-[#12121A] p-7 text-left" data-testid="go-withdraw">
          <span className="w-11 h-11 rounded-xl bg-emerald-500/15 text-emerald-300 flex items-center justify-center"><Bank size={22} weight="bold" /></span>
          <h3 className="font-display text-xl font-bold mt-4">Retirer (CC → EUR)</h3>
          <p className="text-sm text-zinc-400 mt-1">Virement vers votre compte bancaire.</p>
        </motion.button>
      </div>
    </div>
  );
}
