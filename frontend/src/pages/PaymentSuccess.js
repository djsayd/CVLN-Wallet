import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function PaymentSuccess() {
  const navigate = useNavigate();
  const { checkAuth } = useAuth();
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    const sid = new URLSearchParams(window.location.search).get("session_id");
    if (!sid) { setStatus("error"); return; }
    let tries = 0;
    const poll = async () => {
      try {
        const res = await api.get(`/payments/status/${sid}`);
        if (res.data.payment_status === "paid") {
          setStatus("paid");
          toast.success("Dépôt confirmé", { description: `+${res.data.credit_cc} CC crédités` });
          await checkAuth();
          setTimeout(() => navigate("/wallet"), 1800);
          return;
        }
        if (["expired", "failed"].includes(res.data.payment_status)) { setStatus("failed"); return; }
      } catch { }
      if (tries++ < 10) setTimeout(poll, 1500); else setStatus("timeout");
    };
    poll();
  }, [navigate, checkAuth]);

  return (
    <div className="grain min-h-screen flex items-center justify-center bg-[#09090F] text-center p-6">
      <div>
        {status === "checking" && <><div className="w-12 h-12 rounded-full border-2 border-violet-500 border-t-transparent animate-spin mx-auto mb-4" /><p className="text-zinc-400">Vérification du paiement…</p></>}
        {status === "paid" && <><div className="text-5xl mb-3">✅</div><h1 className="font-display text-2xl font-bold">Dépôt confirmé</h1><p className="text-zinc-400 mt-2">Vos CC ont été crédités. Redirection…</p></>}
        {["failed", "error", "timeout"].includes(status) && <><h1 className="font-display text-2xl font-bold">Paiement non confirmé</h1><button onClick={() => navigate("/wallet")} className="mt-4 px-5 py-2.5 rounded-full bg-violet-600 font-semibold">Retour au Wallet</button></>}
      </div>
    </div>
  );
}
