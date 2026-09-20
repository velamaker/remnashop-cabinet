import { AdminNavLauncher } from "@/components/admin/AdminNavLauncher";
import { useT } from "@/i18n/I18nContext";

// Главная админки — плиточная навигация по разделам. Статистика вынесена на
// отдельную страницу /admin/stats («Статистика»).
export default function AdminHomePage() {
  const t = useT();
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-fg">{t("adm.home.title")}</h1>
      <AdminNavLauncher />
    </div>
  );
}
