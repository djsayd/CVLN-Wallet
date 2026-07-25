import "@/index.css";
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useRef } from "react";
import { Toaster } from "sonner";
import api from "@/lib/api";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Wallet from "@/pages/Wallet";
import Transactions from "@/pages/Transactions";
import Coffres from "@/pages/Coffres";
import Convertir from "@/pages/Convertir";
import Marketplace from "@/pages/Marketplace";
import Ecosysteme from "@/pages/Ecosysteme";
import FrekId from "@/pages/FrekId";
import Developers from "@/pages/Developers";
import Parametres from "@/pages/Parametres";

function AuthCallback() {
  const navigate = useNavigate();
  const { setUser } = useAuth();
  const done = useRef(false);

  useEffect(() => {
    if (done.current) return;
    done.current = true;
    const hash = window.location.hash;
    const sessionId = new URLSearchParams(hash.replace("#", "")).get("session_id");
    (async () => {
      try {
        const res = await api.post("/auth/session", { session_id: sessionId });
        setUser(res.data);
        window.history.replaceState(null, "", "/");
        navigate("/", { replace: true });
      } catch {
        navigate("/login", { replace: true });
      }
    })();
  }, [navigate, setUser]);

  return (
    <div className="grain min-h-screen flex items-center justify-center">
      <div className="text-center">
        <div className="w-12 h-12 rounded-full border-2 border-violet-500 border-t-transparent animate-spin mx-auto mb-4" />
        <p className="text-zinc-400">Connexion sécurisée…</p>
      </div>
    </div>
  );
}

function Protected({ children }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-10 h-10 rounded-full border-2 border-violet-500 border-t-transparent animate-spin" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AppRouter() {
  const location = useLocation();
  if (location.hash?.includes("session_id=")) return <AuthCallback />;
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/wallet" element={<Wallet />} />
        <Route path="/transactions" element={<Transactions />} />
        <Route path="/coffres" element={<Coffres />} />
        <Route path="/convertir" element={<Convertir />} />
        <Route path="/marketplace" element={<Marketplace />} />
        <Route path="/ecosysteme" element={<Ecosysteme />} />
        <Route path="/frek-id" element={<FrekId />} />
        <Route path="/developers" element={<Developers />} />
        <Route path="/parametres" element={<Parametres />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Toaster theme="dark" position="top-right" richColors />
        <AppRouter />
      </AuthProvider>
    </BrowserRouter>
  );
}


