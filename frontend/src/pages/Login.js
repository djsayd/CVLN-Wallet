import { motion } from "framer-motion";
import { GoogleLogo, ShieldCheck, Lightning, Vault } from "@phosphor-icons/react";

export default function Login() {
  const handleLogin = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  return (
    <div className="grain min-h-screen flex bg-[#09090F] text-white relative overflow-hidden">
      <div className="pointer-events-none fixed -top-40 -left-40 w-[600px] h-[600px] rounded-full bg-violet-600/20 blur-[140px]" />
      <div className="pointer-events-none fixed bottom-0 right-0 w-[500px] h-[500px] rounded-full bg-cyan-500/10 blur-[140px]" />

      {/* Left brand panel */}
      <div className="hidden lg:flex flex-col justify-between w-[46%] p-14 relative z-10 border-r border-white/5">
        <div className="font-display text-3xl font-extrabold tracking-tight flex items-center gap-3">
          <span className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-cyan-400 flex items-center justify-center text-black font-black">C</span>
          CVLN <span className="text-violet-400">Wallet</span>
        </div>
        <div>
          <motion.h1
            initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}
            className="font-display text-5xl xl:text-6xl font-extrabold tracking-tighter leading-[1.05]"
          >
            Votre infrastructure<br />financière <span className="text-violet-400">culturelle.</span>
          </motion.h1>
          <p className="mt-6 text-zinc-400 text-lg max-w-md leading-relaxed">
            Portefeuille unifié, Jeton CC, coffres et écosystème CVLN — connectés par votre FREK-ID unique.
          </p>
          <div className="mt-10 flex flex-col gap-4">
            {[
              { icon: ShieldCheck, t: "FREK-ID", d: "Identité unique et permanente" },
              { icon: Lightning, t: "Jeton CC", d: "1 JCC = 1,50 € — conversions instantanées" },
              { icon: Vault, t: "Coffres", d: "Épargnez et projetez vos objectifs" },
            ].map((f) => (
              <div key={f.t} className="flex items-center gap-4">
                <div className="w-11 h-11 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-violet-400">
                  <f.icon size={22} weight="duotone" />
                </div>
                <div>
                  <div className="font-semibold">{f.t}</div>
                  <div className="text-sm text-zinc-500">{f.d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="text-xs text-zinc-600">Fintech · Portefeuille numérique · Infrastructure financière</div>
      </div>

      {/* Right auth card */}
      <div className="flex-1 flex items-center justify-center p-6 relative z-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, delay: 0.1 }}
          className="w-full max-w-md bg-[#12121A]/80 backdrop-blur-xl border border-white/10 rounded-3xl p-8 sm:p-10"
        >
          <div className="lg:hidden font-display text-2xl font-extrabold mb-8 flex items-center gap-2">
            <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-cyan-400 flex items-center justify-center text-black font-black">C</span>
            CVLN Wallet
          </div>
          <h2 className="font-display text-3xl font-bold tracking-tight">Bienvenue</h2>
          <p className="text-zinc-400 mt-2 mb-8">Connectez-vous pour accéder à votre espace financier CVLN.</p>

          <button
            onClick={handleLogin}
            data-testid="google-login-btn"
            className="w-full flex items-center justify-center gap-3 bg-white text-black font-semibold py-3.5 rounded-full hover:bg-zinc-100 active:scale-[0.98] transition-transform"
          >
            <GoogleLogo size={22} weight="bold" /> Continuer avec Google
          </button>

          <div className="mt-8 flex items-center gap-3 text-zinc-600 text-sm">
            <ShieldCheck size={18} className="text-emerald-400" />
            Authentification sécurisée · session chiffrée
          </div>
          <p className="mt-6 text-xs text-zinc-600 leading-relaxed">
            En continuant, vous acceptez de lier votre compte à un FREK-ID unique au sein de l'écosystème CVLN Group.
          </p>
        </motion.div>
      </div>
    </div>
  );
}
