import { useEffect, useState } from "react";
import { toast } from "sonner";
import { motion } from "framer-motion";
import { ShoppingBag } from "@phosphor-icons/react";
import api, { fmt } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function Marketplace() {
  const { checkAuth } = useAuth();
  const [items, setItems] = useState([]);
  const [wallet, setWallet] = useState(null);
  const [buying, setBuying] = useState(null);

  const load = async () => {
    const [i, w] = await Promise.all([api.get("/marketplace"), api.get("/wallet")]);
    setItems(i.data); setWallet(w.data);
  };
  useEffect(() => { load(); }, []);

  const buy = async (item) => {
    setBuying(item.item_id);
    try {
      await api.post("/marketplace/buy", { item_id: item.item_id });
      toast.success("Achat réussi", { description: `${item.title} · -${fmt(item.price_cc)} CC` });
      await load(); await checkAuth();
    } catch (e) { toast.error(e.response?.data?.detail || "Erreur"); }
    finally { setBuying(null); }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">Marketplace</h1>
          <p className="text-zinc-400 mt-1">Services et biens de l'écosystème CVLN — payez en CC.</p>
        </div>
        <div className="px-4 py-2.5 rounded-full bg-white/5 border border-white/10 text-sm">Solde : <span className="font-semibold text-violet-400">{fmt(wallet?.balance_cc)} CC</span></div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-5">
        {items.map((it, i) => (
          <motion.div key={it.item_id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
            whileHover={{ y: -4 }} className="rounded-3xl border border-white/10 bg-[#12121A] p-6 flex flex-col" data-testid={`mk-item-${it.item_id}`}>
            <div className="flex items-center justify-between mb-4">
              <span className="w-11 h-11 rounded-xl bg-violet-500/15 text-violet-300 flex items-center justify-center"><ShoppingBag size={22} weight="duotone" /></span>
              {it.tag && <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full bg-cyan-500/10 text-cyan-300">{it.tag}</span>}
            </div>
            <h3 className="font-display text-lg font-bold leading-tight">{it.title}</h3>
            <div className="text-sm text-zinc-500 mt-1">{it.seller} · {it.category}</div>
            <div className="mt-auto pt-5 flex items-center justify-between">
              <span className="font-display text-xl font-black">{fmt(it.price_cc)} <span className="text-sm text-zinc-500">CC</span></span>
              <button onClick={() => buy(it)} disabled={buying === it.item_id} className="px-4 py-2.5 rounded-full bg-gradient-to-r from-violet-600 to-violet-500 font-semibold text-sm active:scale-95 transition-transform disabled:opacity-50" data-testid={`buy-${it.item_id}`}>
                {buying === it.item_id ? "..." : "Acheter"}
              </button>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
