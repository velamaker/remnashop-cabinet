import { AdminNavLauncher } from "@/components/admin/AdminNavLauncher";

// Главная админки — плиточная навигация по разделам. Статистика вынесена на
// отдельную страницу /admin/stats («Статистика»).
export default function AdminHomePage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-fg">Панель управления</h1>
      <AdminNavLauncher />
    </div>
  );
}
