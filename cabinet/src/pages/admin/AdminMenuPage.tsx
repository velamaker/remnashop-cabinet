import { useEffect, useState } from "react";
import { Save, CheckCircle2, SquareMenu } from "lucide-react";
import { menuAdminApi, type MenuConfig } from "@/api/menu";
import { ApiError } from "@/types/api";

type Key = keyof MenuConfig;

const ITEMS: { key: Key; title: string; desc: string }[] = [
  {
    key: "cabinet_miniapp",
    title: "Личный кабинет (Mini App)",
    desc: "Открывает кабинет внутри Telegram. Синяя, основная.",
  },
  {
    key: "cabinet_url",
    title: "Кабинет в браузере",
    desc: "Прямая ссылка на сайт кабинета (резерв, если Mini App не открылся).",
  },
  {
    key: "connect_miniapp",
    title: "Подключиться (Mini App)",
    desc: "Открывает раздел «Устройства» кабинета внутри Telegram.",
  },
  {
    key: "connect_url",
    title: "Подключиться (ссылка)",
    desc: "Раздел «Устройства» кабинета прямой ссылкой в браузере.",
  },
  {
    key: "remna_sub",
    title: "Подписка (резерв)",
    desc: "Стандартная страница подписки Remnawave — на случай, если кабинет недоступен.",
  },
];

export default function AdminMenuPage() {
  const [cfg, setCfg] = useState<MenuConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    menuAdminApi
      .get()
      .then(setCfg)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка загрузки"))
      .finally(() => setLoading(false));
  }, []);

  const toggle = (key: Key) => cfg && setCfg({ ...cfg, [key]: !cfg[key] });

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const next = await menuAdminApi.update(cfg);
      setCfg(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  if (!cfg) return null;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-border-subtle bg-bg/80 px-5 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <h1 className="flex items-center gap-2 text-xl font-bold text-fg md:text-2xl">
          <SquareMenu className="h-5 w-5 text-accent" />
          Меню бота
        </h1>
        <button
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? "Сохранено" : saving ? "Сохранение…" : "Сохранить"}
        </button>
      </div>

      <p className="text-sm text-fg-muted">
        Какие кнопки показывать в главном меню бота. Применяется сразу после
        «Сохранить» — перезапуск не нужен. (Действует, когда веб-кабинет включён.)
      </p>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="space-y-2">
        {ITEMS.map((item) => {
          const on = cfg[item.key];
          return (
            <label
              key={item.key}
              className={`flex cursor-pointer items-center gap-3 rounded-2xl border p-4 transition-colors ${
                on ? "border-border-subtle bg-bg-subtle" : "border-border-subtle bg-bg opacity-70"
              }`}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => toggle(item.key)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              <div className="min-w-0">
                <span className="text-sm font-semibold text-fg">{item.title}</span>
                <p className="mt-0.5 text-xs text-fg-muted">{item.desc}</p>
              </div>
            </label>
          );
        })}
      </div>

      <p className="text-xs text-fg-subtle">
        Если выключить все — в меню останутся только базовые разделы (Устройства,
        Подписка и т.д.). Когда веб-кабинет выключен, показывается стандартная
        кнопка подписки Remnawave.
      </p>
    </div>
  );
}
