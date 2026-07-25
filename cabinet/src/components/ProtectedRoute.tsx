import { type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { AppLayout } from "@/components/layout/AppLayout";
import { safeInternalPath as safeNext } from "@/lib/nav";

function FullScreenLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
    </div>
  );
}

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) return <FullScreenLoader />;
  if (!user) {
    // Сохраняем, куда вели (напр. /devices из кнопки «Подключиться»), чтобы после
    // авто-входа в Mini App вернуть пользователя именно туда, а не на главную.
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  return <AppLayout>{children}</AppLayout>;
}

export function PublicOnlyRoute({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) return <FullScreenLoader />;
  if (user) {
    const params = new URLSearchParams(location.search);
    return <Navigate to={safeNext(params.get("next"))} replace />;
  }

  return <>{children}</>;
}
