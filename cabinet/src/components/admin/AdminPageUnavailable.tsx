import { Link } from "react-router-dom";
import { Info, ArrowLeft } from "lucide-react";

/**
 * Экран вместо страницы админки, которой на ЭТОЙ установке нет.
 *
 * Кабинет один, а бэкенды под ним разные: часть экранов поверх чужого бота не
 * работает и в меню не показывается (whoami.pages). Но адрес остаётся рабочим —
 * по нему приходят из закладок и просто по памяти, и раньше такая страница молча
 * открывалась пустой: заголовок есть, данных нет, ни слова почему. Это читается
 * как поломка, поэтому объясняем прямо.
 *
 * `note` — причина словами от бэкенда (whoami.page_notes). Не прислал — значит
 * причина техническая и общая, её и пишем.
 */
export function AdminPageUnavailable({ path, note }: { path: string; note?: string }) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Info className="h-[18px] w-[18px] text-fg-muted" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Раздела здесь нет</h1>
      </div>

      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <p className="text-sm leading-relaxed text-fg-muted">
          {note ||
            "Бэкенд, к которому подключён этот кабинет, такую страницу не поддерживает — поэтому её нет и в меню админки."}
        </p>
        <p className="mt-3 text-xs text-fg-subtle">
          Адрес: <code className="rounded bg-bg px-1.5 py-0.5">{path}</code>
        </p>
        <Link
          to="/admin"
          className="mt-4 inline-flex items-center gap-2 rounded-xl border border-border-subtle bg-bg px-4 py-2 text-sm font-medium text-fg hover:bg-bg-raised"
        >
          <ArrowLeft className="h-4 w-4" /> Вернуться в админку
        </Link>
      </section>
    </div>
  );
}
