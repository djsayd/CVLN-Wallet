import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { Fingerprint, SealCheck, Copy, ShieldCheck, Certificate, Clock } from "@phosphor-icons/react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const CERTS = [
  { title: "FREK-ID émis", org: "FREKCORE", date: "Actif" },
  { title: "Créateur Premium", org: "CVLN Academy", date: "2026" },
  { title: "Certification culturelle", org: "Kiltikonet", date: "CC2026" },
];

export default function FrekId() {
  const { user } = useAuth();
  const [wallet, setWallet] = useState(null);
  useEffect(() => { (async () => setWallet((await api.get("/wallet")).data))(); }, []);
  const score = wallet?.frek_score ?? 978;
  const pct = (score / 1000) * 100;
  const R = 52, C = 2 * Math.PI * R;

  const copyId = () => { navigator.clipboard.writeText(user?.frek_id || ""); toast.success("FREK-ID copié"); };

  return (
    <div className="space-y-6">
      <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">FREK-ID</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Identity card */}
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="lg:col-span-2 relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-violet-600/20 via-[#12121A] to-[#12121A] p-8">
          <div className="absolute -right-10 -top-10 w-52 h-52 rounded-full bg-violet-500/20 blur-3xl" />
          <div className="flex items-center gap-4">
            {user?.picture
              ? <img src={user.picture} alt="" className="w-20 h-20 rounded-2xl object-cover border border-white/10" />
              : <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-violet-500 to-cyan-400 flex items-center justify-center"><Fingerprint size={36} weight="duotone" className="text-black" /></div>}
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-display text-2xl font-bold">{user?.name}</h2>
                <SealCheck size={22} weight="fill" className="text-violet-400" />
              </div>
              <div className="text-zinc-400">{user?.email}</div>
              <div className="text-sm mt-1 text-zinc-500">Niveau {user?.frek_level}</div>
            </div>
          </div>
          <div className="mt-8 flex items-center justify-between bg-white/5 border border-white/10 rounded-2xl px-5 py-4">
            <div>
              <div className="text-xs uppercase tracking-widest text-zinc-500">Identifiant unique</div>
              <div className="font-mono text-lg font-bold text-violet-300" data-testid="frek-id-value">{user?.frek_id}</div>
            </div>
            <button onClick={copyId} className="p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors" data-testid="copy-frek-id"><Copy size={20} /></button>
          </div>
          <div className="mt-3 flex items-center gap-2 text-sm text-zinc-500"><ShieldCheck size={16} className="text-emerald-400" /> Identité permanente & vérifiable dans tout l'écosystème CVLN</div>
        </motion.div>

        {/* Score gauge */}
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-7 flex flex-col items-center justify-center">
          <div className="text-xs font-semibold tracking-widest uppercase text-zinc-500 mb-4">FREK Score</div>
          <div className="relative w-36 h-36">
            <svg className="w-full h-full -rotate-90">
              <circle cx="72" cy="72" r={R} fill="none" stroke="rgba(255,255,255,.08)" strokeWidth="12" />
              <motion.circle cx="72" cy="72" r={R} fill="none" stroke="url(#g)" strokeWidth="12" strokeLinecap="round"
                strokeDasharray={C} initial={{ strokeDashoffset: C }} animate={{ strokeDashoffset: C - (pct / 100) * C }} transition={{ duration: 1.2, ease: "easeOut" }} />
              <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#8B5CF6" /><stop offset="100%" stopColor="#00F0FF" /></linearGradient></defs>
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="font-display text-4xl font-black text-violet-400">{score}</span>
              <span className="text-xs text-zinc-500">/ 1000</span>
            </div>
          </div>
          <div className="mt-4 text-sm text-emerald-400 font-semibold">Excellent</div>
        </motion.div>
      </div>

      {/* Certifications */}
      <div className="rounded-3xl border border-white/10 bg-[#12121A] p-6 sm:p-7">
        <h2 className="font-display text-xl font-bold mb-5 flex items-center gap-2"><Certificate size={22} className="text-violet-400" /> Certifications FREKCORE</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {CERTS.map((c) => (
            <div key={c.title} className="rounded-2xl bg-white/5 border border-white/10 p-5" data-testid={`cert-${c.title}`}>
              <SealCheck size={24} weight="fill" className="text-violet-400" />
              <div className="font-semibold mt-3">{c.title}</div>
              <div className="text-sm text-zinc-500">{c.org}</div>
              <div className="text-xs text-zinc-600 mt-2 flex items-center gap-1"><Clock size={13} /> {c.date}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
