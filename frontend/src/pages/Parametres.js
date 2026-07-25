import { motion } from "framer-motion";
import { User, Bell, ShieldCheck, Globe, SignOut } from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";

export default function Parametres() {
  const { user, logout } = useAuth();

  const rows = [
    { icon: User, label: "Nom", value: user?.name },
    { icon: Globe, label: "Email", value: user?.email },
    { icon: ShieldCheck, label: "FREK-ID", value: user?.frek_id },
  ];

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Paramètres</h1>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-7">
        <h2 className="font-display text-lg font-bold mb-5">Profil</h2>
        <div className="divide-y divide-white/5">
          {rows.map((r) => (
            <div key={r.label} className="flex items-center gap-4 py-4">
              <span className="w-10 h-10 rounded-xl bg-white/5 flex items-center justify-center text-violet-400"><r.icon size={20} weight="duotone" /></span>
              <div className="flex-1">
                <div className="text-xs uppercase tracking-widest text-zinc-500">{r.label}</div>
                <div className="font-semibold">{r.value}</div>
              </div>
            </div>
          ))}
        </div>
      </motion.div>

      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-7">
        <h2 className="font-display text-lg font-bold mb-4 flex items-center gap-2"><Bell size={20} className="text-violet-400" /> Préférences</h2>
        <div className="flex items-center justify-between py-3">
          <span className="text-zinc-300">Notifications de transactions</span>
          <div className="w-11 h-6 rounded-full bg-violet-600 relative"><span className="absolute right-1 top-1 w-4 h-4 rounded-full bg-white" /></div>
        </div>
        <div className="flex items-center justify-between py-3">
          <span className="text-zinc-300">Alertes de sécurité</span>
          <div className="w-11 h-6 rounded-full bg-violet-600 relative"><span className="absolute right-1 top-1 w-4 h-4 rounded-full bg-white" /></div>
        </div>
      </div>

      <button onClick={logout} data-testid="settings-logout" className="w-full py-3.5 rounded-full bg-red-500/10 text-red-400 border border-red-500/20 font-semibold hover:bg-red-500/15 active:scale-95 transition-transform flex items-center justify-center gap-2">
        <SignOut size={20} /> Se déconnecter
      </button>

      <p className="text-xs text-zinc-600 text-center">CVLN Wallet · Fintech & infrastructure financière — jamais une banque.</p>
    </div>
  );
}
