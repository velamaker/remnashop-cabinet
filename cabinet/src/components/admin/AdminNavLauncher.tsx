import { Link } from "react-router-dom";
import { navGroups } from "@/components/layout/AdminLayout";
import { useAuth } from "@/contexts/AuthContext";

// Плиточный лаунчер разделов — главная страница админки. Все разделы собраны по
// группам колонками; отсюда открывается остальная навигация.
export function AdminNavLauncher() {
  const { canSection } = useAuth();
  const groups = navGroups
    .map((g) => ({ ...g, items: g.items.filter((it) => canSection(it.section)) }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {groups.map((group) => (
        <div key={group.title} className="rounded-2xl border border-border-subtle bg-bg-subtle p-4">
          <p className="mb-2.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
            {group.title}
          </p>
          <div className="flex flex-col gap-0.5">
            {group.items.map(({ to, icon: Icon, label }) => (
              <Link
                key={to}
                to={to}
                className="group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-fg-muted transition-colors hover:bg-bg-raised hover:text-fg"
              >
                <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-bg-raised text-fg-muted transition-colors group-hover:bg-accent-subtle group-hover:text-accent">
                  <Icon className="h-4 w-4" strokeWidth={1.75} />
                </span>
                <span className="truncate">{label}</span>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
