import { useEffect, useState } from "react";
import { Save, CheckCircle2, Star, Smartphone, Plus, Trash2, RefreshCw, Link2 } from "lucide-react";
import { appsAdminApi, type CustomApp } from "@/api/apps";
import { APPS, PLATFORMS, DEFAULT_PRIORITY } from "@/data/apps";
import { ApiError } from "@/types/api";

const EMPTY_CUSTOM = { name: "", desc: "", deep_link: "", install_url: "", platforms: [] as string[] };

// Upstream-источник актуальных ссылок установки (Remnawave его поддерживает).
// Локальная страница подписки (sub-домен) не отдаёт app-config.json наружу,
// поэтому по умолчанию берём официальный репозиторий.
const RECOMMENDED_LINKS_SOURCE =
  "https://raw.githubusercontent.com/remnawave/subscription-page/main/frontend/public/assets/app-config.json";

export default function AdminAppsPage() {
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [priority, setPriority] = useState<string | null>(null);
  const [custom, setCustom] = useState<CustomApp[]>([]);
  const [draft, setDraft] = useState({ ...EMPTY_CUSTOM });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Авто-подтяжка ссылок установки из upstream app-config.json.
  const [linksSourceUrl, setLinksSourceUrl] = useState("");
  const [linksUpdatedAt, setLinksUpdatedAt] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);

  useEffect(() => {
    appsAdminApi
      .get()
      .then((cfg) => {
        // enabled === null → показываются все приложения
        setEnabled(new Set(cfg.enabled ?? APPS.map((a) => a.id)));
        setPriority(cfg.priority);
        setCustom(cfg.custom ?? []);
        // Пусто → подставляем рекомендуемый upstream-источник (готов к «Обновить»).
        setLinksSourceUrl(cfg.links_source_url || RECOMMENDED_LINKS_SOURCE);
        setLinksUpdatedAt(cfg.links_updated_at ?? null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка загрузки"))
      .finally(() => setLoading(false));
  }, []);

  const refreshLinks = async () => {
    setRefreshing(true);
    setRefreshMsg(null);
    setError(null);
    try {
      const r = await appsAdminApi.refreshLinks(linksSourceUrl.trim() || undefined);
      setLinksUpdatedAt(r.updated_at);
      setRefreshMsg(`Обновлено ссылок для ${r.count} приложений: ${r.apps.join(", ")}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось обновить ссылки");
    } finally {
      setRefreshing(false);
    }
  };

  const toggleDraftPlatform = (p: string) =>
    setDraft((d) => ({
      ...d,
      platforms: d.platforms.includes(p) ? d.platforms.filter((x) => x !== p) : [...d.platforms, p],
    }));

  const addCustom = () => {
    const name = draft.name.trim();
    const deep = draft.deep_link.trim();
    if (!name || !deep) { setError("Укажите название и deep-link (со вставкой {sub})"); return; }
    setError(null);
    setCustom((prev) => [
      ...prev,
      {
        id: "",  // сервер сгенерирует
        name,
        desc: draft.desc.trim(),
        platforms: draft.platforms.length ? draft.platforms : PLATFORMS.map((p) => p.id),
        deep_link: deep,
        install_url: draft.install_url.trim() || null,
      },
    ]);
    setDraft({ ...EMPTY_CUSTOM });
  };

  const removeCustom = (i: number) => setCustom((prev) => prev.filter((_, idx) => idx !== i));

  const toggle = (id: string) => {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        if (priority === id) setPriority(null); // выключили приоритетное — сбрасываем
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await appsAdminApi.update({
        priority: priority || null,
        enabled: Array.from(enabled),
        custom,
        links_source_url: linksSourceUrl.trim() || null,
      });
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

  const effectivePriority = priority || DEFAULT_PRIORITY;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Sticky header */}
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-border-subtle bg-bg/80 px-5 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <h1 className="flex items-center gap-2 text-xl font-bold text-fg md:text-2xl">
          <Smartphone className="h-5 w-5 text-accent" />
          Приложения
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
        Отметьте приложения, которые показывать пользователям на странице
        «Подключить устройство». Звёздочкой выберите{" "}
        <span className="text-fg">приоритетное</span> — оно встанет первым и с
        пометкой «Рекомендуем».
      </p>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="space-y-2">
        {APPS.map((app) => {
          const on = enabled.has(app.id);
          const isPriority = effectivePriority === app.id && on;
          return (
            <div
              key={app.id}
              className={`flex items-center gap-3 rounded-2xl border p-4 transition-colors ${
                on ? "border-border-subtle bg-bg-subtle" : "border-border-subtle bg-bg opacity-60"
              }`}
            >
              {/* Чекбокс «показывать» */}
              <label className="flex flex-1 cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(app.id)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-semibold text-fg">{app.name}</span>
                    {isPriority && (
                      <span className="inline-flex flex-shrink-0 items-center gap-1 rounded-full border border-accent/30 bg-accent-subtle px-2 py-0.5 text-[10px] font-medium text-accent">
                        <Star className="h-3 w-3" />
                        Рекомендуем
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-fg-muted">
                    {app.desc} · {app.platforms.join(", ")}
                  </p>
                </div>
              </label>

              {/* Сделать приоритетным */}
              <button
                type="button"
                onClick={() => on && setPriority(app.id)}
                disabled={!on}
                title={on ? "Сделать приоритетным" : "Сначала включите приложение"}
                className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border transition-colors ${
                  isPriority
                    ? "border-accent bg-accent-subtle text-accent"
                    : "border-border-subtle text-fg-subtle hover:text-fg disabled:opacity-40"
                }`}
              >
                <Star className={`h-4 w-4 ${isPriority ? "fill-current" : ""}`} />
              </button>
            </div>
          );
        })}
      </div>

      <p className="text-xs text-fg-subtle">
        Если не отмечено ни одно приложение, у пользователей будет пусто — оставьте
        хотя бы одно.
      </p>

      {/* Авто-подтяжка ссылок установки */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
          <Link2 className="h-4 w-4 text-accent" />
          Актуальные ссылки установки
        </h2>
        <p className="mt-0.5 text-xs text-fg-muted">
          Ссылки на скачивание (особенно iOS App Store) меняются при переиздании
          приложения в сторе. Источник <span className="font-mono">app-config.json</span>{" "}
          от Remnawave подтягивается автоматически (раз в сутки) и заменяет устаревшие
          встроенные ссылки по совпадающим приложениям (Happ, Streisand, Shadowrocket
          и др.). По умолчанию — официальный репозиторий Remnawave; можно указать свой.
        </p>

        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            className="input flex-1"
            placeholder="https://sub.example.com/assets/app-config.json"
            value={linksSourceUrl}
            onChange={(e) => setLinksSourceUrl(e.target.value)}
          />
          <button
            type="button"
            onClick={refreshLinks}
            disabled={refreshing || !linksSourceUrl.trim()}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-border-subtle bg-bg px-4 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Обновление…" : "Обновить сейчас"}
          </button>
        </div>

        <p className="mt-2 text-xs text-fg-subtle">
          {linksUpdatedAt
            ? `Ссылки обновлены: ${new Date(linksUpdatedAt).toLocaleString("ru")}`
            : "Ссылки ещё не подтягивались — сохраните URL и нажмите «Обновить сейчас»."}
        </p>
        {refreshMsg && <p className="mt-1 text-xs text-success">{refreshMsg}</p>}
      </section>

      {/* Свои приложения */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="text-sm font-semibold text-fg">Свои приложения</h2>
        <p className="mt-0.5 text-xs text-fg-muted">
          Добавьте собственный клиент. В <span className="text-fg">deep-link</span> вставьте{" "}
          <span className="text-fg font-mono">{"{sub}"}</span> — туда подставится ссылка подписки
          (напр. <span className="font-mono">myvpn://add/{"{sub}"}</span>).
        </p>

        {/* Список добавленных */}
        {custom.length > 0 && (
          <div className="mt-4 space-y-2">
            {custom.map((c, i) => (
              <div key={i} className="flex items-center gap-3 rounded-xl border border-border-subtle bg-bg p-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-fg">{c.name}</p>
                  <p className="truncate text-xs text-fg-muted">
                    <span className="font-mono">{c.deep_link}</span> · {c.platforms.join(", ")}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => removeCustom(i)}
                  className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-danger/30 bg-danger/5 text-danger hover:bg-danger/10"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Форма добавления */}
        <div className="mt-4 space-y-2 rounded-xl border border-border-subtle bg-bg p-4">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <input className="input" placeholder="Название (напр. MyVPN)" value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <input className="input" placeholder="Описание (необязательно)" value={draft.desc}
              onChange={(e) => setDraft({ ...draft, desc: e.target.value })} />
          </div>
          <input className="input font-mono" placeholder="deep-link: myvpn://add/{sub}" value={draft.deep_link}
            onChange={(e) => setDraft({ ...draft, deep_link: e.target.value })} />
          <input className="input" placeholder="Ссылка установки (необязательно)" value={draft.install_url}
            onChange={(e) => setDraft({ ...draft, install_url: e.target.value })} />
          <div className="flex flex-wrap gap-2 pt-1">
            {PLATFORMS.map((p) => (
              <button key={p.id} type="button" onClick={() => toggleDraftPlatform(p.id)}
                className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                  draft.platforms.includes(p.id)
                    ? "border-accent bg-accent-subtle text-accent"
                    : "border-border-subtle text-fg-muted hover:text-fg"
                }`}>
                {p.label}
              </button>
            ))}
            <span className="self-center text-[11px] text-fg-subtle">
              {draft.platforms.length ? "" : "не выбрано = все платформы"}
            </span>
          </div>
          <button type="button" onClick={addCustom}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-bg-subtle px-3 py-1.5 text-sm font-medium text-fg hover:bg-bg-overlay">
            <Plus className="h-4 w-4" /> Добавить приложение
          </button>
        </div>
        <p className="mt-2 text-[11px] text-fg-subtle">Не забудьте «Сохранить» вверху.</p>
      </section>
    </div>
  );
}
