import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import { Plus, Trash, ArrowUp, ArrowDown, X, Vault as VaultIcon } from "@phosphor-icons/react";
import api, { fmt } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function Coffres() {
  const { checkAuth } = useAuth();
  const [coffres, setCoffres] = useState([]);
  const [wallet, setWallet] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [move, setMove] = useState(null); // {coffre, dir}
  const [moveAmt, setMoveAmt] = useState("");

  const load = async () => {
    const [c, w] = await Promise.all([api.get("/coffres"), api.get("/wallet")]);
    setCoffres(c.data); setWallet(w.data);
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!name) return toast.error("Nommez votre coffre");
    try {
      await api.post("/coffres", { name, goal_cc: parseFloat(goal) || 50000 });
      toast.success("Coffre créé");
      setShowCreate(false); setName(""); setGoal(""); await load();
    } catch { toast.error("Erreur"); }
  };

  const doMove = async () => {
    const amt = parseFloat(moveAmt);
    if (!amt || amt <= 0) return toast.error("Montant invalide");
    const signed = move.dir === "in" ? amt : -amt;
    try {
      await api.post(`/coffres/${move.coffre.coffre_id}/move`, { amount: signed });
      toast.success(move.dir === "in" ? "Dépôt effectué" : "Retrait effectué");
      setMove(null); setMoveAmt(""); await load(); await checkAuth();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
  };

  const del = async (id) => {
    try { await api.delete(`/coffres/${id}`); toast.success("Coffre supprimé"); await load(); await checkAuth(); }
    catch { toast.error("Erreur"); }
  };

  const inputCls = "w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-violet-500 text-white placeholder:text-zinc-500";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Coffres</h1>
          <p className="text-zinc-400 mt-1">Disponible : <span className="text-white font-semibold">{fmt(wallet?.balance_cc)} CC</span></p>
        </div>
        <button onClick={() => setShowCreate(true)} data-testid="open-create-coffre" className="px-5 py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold glow-violet active:scale-95 transition-transform flex items-center gap-2">
          <Plus size={18} weight="bold" /> Nouveau coffre
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {coffres.map((c) => {
          const pct = Math.min(100, Math.round((c.amount_cc / c.goal_cc) * 100));
          return (
            <motion.div key={c.coffre_id} whileHover={{ y: -4 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-6 relative overflow-hidden" data-testid={`coffre-${c.coffre_id}`}>
              <div className="absolute -right-6 -top-6 w-32 h-32 rounded-full blur-3xl opacity-30" style={{ background: c.color }} />
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <span className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ background: `${c.color}22`, color: c.color }}><VaultIcon size={22} weight="duotone" /></span>
                  <div>
                    <h3 className="font-display text-lg font-bold">{c.name}</h3>
                    <div className="text-sm text-zinc-500">Objectif {fmt(c.goal_cc)} CC</div>
                  </div>
                </div>
                <button onClick={() => del(c.coffre_id)} className="text-zinc-500 hover:text-red-400 transition-colors" data-testid={`delete-${c.coffre_id}`}><Trash size={18} /></button>
              </div>
              <div className="font-display text-3xl font-black tracking-tight mt-5">{fmt(c.amount_cc)} <span className="text-lg text-zinc-500">CC</span></div>
              <div className="h-2.5 rounded-full bg-white/10 mt-4 overflow-hidden">
                <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${c.color}, #00F0FF)` }} />
              </div>
              <div className="flex items-center justify-between mt-2 text-xs text-zinc-500"><span>{pct}%</span><span>{fmt(c.goal_cc - c.amount_cc)} CC restants</span></div>
              <div className="flex gap-2 mt-5">
                <button onClick={() => { setMove({ coffre: c, dir: "in" }); }} className="flex-1 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 font-semibold text-sm flex items-center justify-center gap-1.5 transition-colors" data-testid={`deposit-${c.coffre_id}`}><ArrowUp size={16} weight="bold" /> Déposer</button>
                <button onClick={() => { setMove({ coffre: c, dir: "out" }); }} className="flex-1 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 font-semibold text-sm flex items-center justify-center gap-1.5 transition-colors" data-testid={`withdraw-${c.coffre_id}`}><ArrowDown size={16} weight="bold" /> Retirer</button>
              </div>
            </motion.div>
          );
        })}
      </div>

      <AnimatePresence>
        {showCreate && (
          <Modal onClose={() => setShowCreate(false)} title="Nouveau coffre">
            <div className="space-y-3">
              <input className={inputCls} placeholder="Nom du coffre" value={name} onChange={(e) => setName(e.target.value)} data-testid="new-coffre-name" />
              <input className={inputCls} type="number" placeholder="Objectif (CC)" value={goal} onChange={(e) => setGoal(e.target.value)} data-testid="new-coffre-goal" />
              <button onClick={create} className="w-full py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold active:scale-95 transition-transform" data-testid="confirm-create-coffre">Créer le coffre</button>
            </div>
          </Modal>
        )}
        {move && (
          <Modal onClose={() => setMove(null)} title={`${move.dir === "in" ? "Déposer vers" : "Retirer de"} ${move.coffre.name}`}>
            <div className="space-y-3">
              <input className={inputCls} type="number" placeholder="Montant (CC)" value={moveAmt} onChange={(e) => setMoveAmt(e.target.value)} data-testid="move-amount" autoFocus />
              <button onClick={doMove} className="w-full py-3 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold active:scale-95 transition-transform" data-testid="confirm-move">Confirmer</button>
            </div>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  );
}

function Modal({ children, title, onClose }) {
  return (
    <motion.div className="fixed inset-0 z-50 flex items-center justify-center p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <motion.div initial={{ scale: 0.95, y: 10 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, opacity: 0 }}
        className="relative w-full max-w-md bg-[#12121A] border border-white/10 rounded-3xl p-7">
        <div className="flex items-center justify-between mb-5">
          <h3 className="font-display text-xl font-bold">{title}</h3>
          <button onClick={onClose} className="text-zinc-500 hover:text-white"><X size={22} /></button>
        </div>
        {children}
      </motion.div>
    </motion.div>
  );
}
