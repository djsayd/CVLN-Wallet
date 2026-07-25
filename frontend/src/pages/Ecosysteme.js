import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  MusicNote, Waveform, Confetti, Globe, Handshake, GraduationCap,
  Stack, Brain, Fingerprint, Cpu, SealCheck,
} from "@phosphor-icons/react";
import api from "@/lib/api";

const ICONS = { music: MusicNote, waveform: Waveform, confetti: Confetti, globe: Globe, handshake: Handshake, graduation: GraduationCap, stack: Stack, brain: Brain, fingerprint: Fingerprint, cpu: Cpu };

export default function Ecosysteme() {
  const [items, setItems] = useState([]);
  useEffect(() => { (async () => setItems((await api.get("/ecosysteme")).data))(); }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Écosystème CVLN</h1>
        <p className="text-zinc-400 mt-1">Chaque entité possède son wallet — reliées par le Jeton CC et votre FREK-ID.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-5">
        {items.map((e, i) => {
          const Icon = ICONS[e.icon] || Globe;
          const active = e.status.startsWith("Actif");
          return (
            <motion.div key={e.name} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
              whileHover={{ y: -4 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-6" data-testid={`eco-${i}`}>
              <div className="flex items-start justify-between">
                <span className="w-12 h-12 rounded-2xl bg-gradient-to-br from-violet-500/20 to-cyan-500/10 text-violet-300 flex items-center justify-center"><Icon size={24} weight="duotone" /></span>
                <span className={`text-[11px] font-semibold px-2.5 py-1 rounded-full flex items-center gap-1 ${active ? "bg-emerald-500/10 text-emerald-400" : "bg-white/5 text-zinc-400"}`}>
                  {active && <SealCheck size={13} weight="fill" />} {e.status}
                </span>
              </div>
              <h3 className="font-display text-lg font-bold mt-4">{e.name}</h3>
              <p className="text-sm text-zinc-500 mt-1">{e.role}</p>
              <div className="mt-4 text-[11px] font-semibold tracking-widest uppercase text-violet-400/80">{e.layer}</div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
