import { Navigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { AppLayout } from "@/components/layout/AppLayout";
import { getTelegramWebApp } from "@/hooks/useTelegramWebApp";
import HomePage from "@/pages/HomePage";
import LandingPage from "@/pages/LandingPage";

function FullScreenLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
    </div>
  );
}

/**
 * Корень «/»:
 *  - вошедший пользователь → кабинет (HomePage в AppLayout);
 *  - Telegram Mini App без сессии → /login (там авто-вход по initData);
 *  - остальные анонимы → публичный лендинг.
 */
export default function RootPage() {
  const { user, isLoading } = useAuth();

  if (isLoading) return <FullScreenLoader />;

  if (!user) {
    const isMiniApp =
      Boolean(window.__tgWebAppExpected) || Boolean(getTelegramWebApp()?.initData);
    if (isMiniApp) return <Navigate to="/login" replace />;
    return <LandingPage />;
  }

  return (
    <AppLayout>
      <HomePage />
    </AppLayout>
  );
}
