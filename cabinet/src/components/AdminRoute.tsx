import { type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { AdminLayout } from "@/components/layout/AdminLayout";

function FullScreenLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
    </div>
  );
}

export function AdminRoute({ children }: { children: ReactNode }) {
  const { user, isAdmin, isLoading } = useAuth();

  if (isLoading) return <FullScreenLoader />;
  if (!user) return <Navigate to="/login" replace />;
  // fail-closed: пускаем только подтверждённых админов (ADMIN/DEV/OWNER)
  if (!isAdmin) return <Navigate to="/" replace />;

  return <AdminLayout>{children}</AdminLayout>;
}
