import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useState } from "react";
import { motion } from "framer-motion";
import {
  House, Wallet as WalletIcon, Receipt, Vault, ArrowsLeftRight,
  Storefront, Planet, IdentificationCard, Gear, SignOut, List, X, MagnifyingGlass, Code, ShieldStar,
} from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";

const NAV = [
  { to: "/", label: "Dashboard", icon: House },
  { to: "/wallet", label: "Wallet", icon: WalletIcon },
  { to: "/transactions", label: "Transactions", icon: Receipt },
  { to: "/coffres", label: "Coffres", icon: Vault },
  { to: "/convertir", label: "Convertir", icon: ArrowsLeftRight },
  { to: "/marketplace", label: "Marketplace", icon: Storefront },
  { to: "/ecosysteme", label: "Écosystème", icon: Planet },
  { to: "/frek-id", label: "FREK-ID", icon: IdentificationCard },
  { to: "/parametres", label: "Paramètres", icon: Gear },
];

const ADMIN_NAV = [
  { to: "/developers", label: "API", icon: Code },
  { to: "/admin", label: "Admin", icon: ShieldStar },
];

function SidebarContent({ onNav }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const items = user?.is_admin ? [...NAV, ...ADMIN_NAV] : NAV;
  return (
    <div className="flex flex-col h-full">
      <div className="px-7 pt-8 pb-10">
        <div className="font-display text-2xl font-extrabold tracking-tight flex items-center gap-2">
          <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-cyan-400 flex items-center justify-center text-black text-sm font-black">C</span>
          CVLN <span className="text-violet-400">Wallet</span>
        </div>
      </div>
      <nav className="flex-1 px-4 flex flex-col gap-1">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            onClick={onNav}
            data-testid={`nav-${item.label.toLowerCase().replace(/[^a-z]/g, "")}`}
            className={({ isActive }) =>
              `group flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-colors relative ${
                isActive ? "text-white bg-white/5" : "text-zinc-400 hover:text-white hover:bg-white/5"
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && <span className="absolute left-0 top-2 bottom-2 w-1 rounded-full bg-gradient-to-b from-violet-500 to-cyan-400" />}
                <item.icon size={20} weight={isActive ? "fill" : "regular"} className={isActive ? "text-violet-400" : ""} />
                {item.label}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="p-4">
        <button
          onClick={() => navigate("/coffres")}
          data-testid="create-coffre-btn"
          className="w-full py-3 rounded-xl bg-gradient-to-r from-violet-600 to-violet-500 text-white font-semibold text-sm glow-violet hover:from-violet-500 hover:to-violet-400 active:scale-95 transition-transform"
        >
          + Créer un Coffre
        </button>
        <button
          onClick={logout}
          data-testid="logout-btn"
          className="w-full mt-2 py-2.5 rounded-xl text-zinc-400 hover:text-white hover:bg-white/5 font-medium text-sm flex items-center justify-center gap-2 transition-colors"
        >
          <SignOut size={18} /> Déconnexion
        </button>
      </div>
    </div>
  );
}

export default function Layout() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const initials = (user?.name || "U").split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="grain min-h-screen flex bg-[#09090F] relative">
      {/* ambient glows */}
      <div className="pointer-events-none fixed -top-40 -left-40 w-[500px] h-[500px] rounded-full bg-violet-600/10 blur-[120px]" />
      <div className="pointer-events-none fixed top-1/3 right-0 w-[400px] h-[400px] rounded-full bg-cyan-500/5 blur-[120px]" />

      {/* Desktop sidebar */}
      <aside className="hidden lg:block w-[264px] shrink-0 bg-[#050508] border-r border-white/10 sticky top-0 h-screen z-20">
        <SidebarContent />
      </aside>

      {/* Mobile drawer */}
      {open && (
        <div className="lg:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-0 h-full w-[264px] bg-[#050508] border-r border-white/10">
            <SidebarContent onNav={() => setOpen(false)} />
          </div>
        </div>
      )}

      <main className="flex-1 min-w-0 relative z-10">
        <header className="sticky top-0 z-30 backdrop-blur-2xl bg-[#09090F]/70 border-b border-white/10 px-5 sm:px-8 py-4 flex items-center gap-3">
          <button className="lg:hidden text-white" onClick={() => setOpen(true)} data-testid="mobile-menu-btn">
            {open ? <X size={24} /> : <List size={24} />}
          </button>
          <div className="hidden sm:flex items-center gap-2 bg-white/5 border border-white/10 rounded-full px-4 py-2.5 w-[340px] max-w-full text-zinc-500">
            <MagnifyingGlass size={18} />
            <input
              placeholder="Rechercher une transaction..."
              className="bg-transparent outline-none text-sm text-white placeholder:text-zinc-500 w-full"
              data-testid="search-input"
            />
          </div>
          <div className="ml-auto flex items-center gap-3">
            <div className="text-right hidden sm:block">
              <div className="text-sm font-semibold leading-tight">{user?.name}</div>
              <div className="text-[11px] text-violet-400 font-mono">{user?.frek_id}</div>
            </div>
            {user?.picture ? (
              <img src={user.picture} alt="" className="w-11 h-11 rounded-full object-cover border border-white/10" />
            ) : (
              <div className="w-11 h-11 rounded-full bg-gradient-to-br from-violet-500 to-cyan-400 flex items-center justify-center font-bold text-black">{initials}</div>
            )}
          </div>
        </header>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: "easeOut" }}
          className="p-5 sm:p-8 max-w-[1400px]"
        >
          <Outlet />
        </motion.div>
      </main>
    </div>
  );
}
