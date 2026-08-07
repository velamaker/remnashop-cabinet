import { type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AdminPageUnavailable } from "@/components/admin/AdminPageUnavailable";

function FullScreenLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
    </div>
  );
}

export function AdminRoute({ children }: { children: ReactNode }) {
  const { user, isAdmin, isLoading, pages, canPage, pageNote } = useAuth();
  const { pathname } = useLocation();

  if (isLoading) return <FullScreenLoader />;
  if (!user) return <Navigate to="/login" replace />;
  // fail-closed: пускаем только подтверждённых админов (ADMIN/DEV/OWNER)
  if (!isAdmin) return <Navigate to="/" replace />;

  // Страницу, которой бэкенд не умеет, кабинет убирает из меню — но адрес живой,
  // и по нему приходят из закладок и по памяти. Раньше она просто открывалась и
  // не могла ничего загрузить: у «Безопасности», например, оставался один
  // заголовок над пустотой, что читается как поломка. Показываем причину.
  //
  // Вложенные адреса (`/admin/users/42`) считаем частью своей страницы: в pages
  // перечислены только корни экранов, и сравнение «в лоб» закрыло бы карточку
  // внутри открытого раздела. Когда списка нет вовсе (наш собственный бэкенд его
  // не шлёт), canPage отвечает «доступна» и сюда мы не заходим вообще.
  const nested = (pages ?? []).some((p) => p !== "/admin" && pathname.startsWith(p + "/"));
  if (!canPage(pathname) && !nested) {
    return (
      <AdminLayout>
        <AdminPageUnavailable path={pathname} note={pageNote(pathname)} />
      </AdminLayout>
    );
  }

  return <AdminLayout>{children}</AdminLayout>;
}
