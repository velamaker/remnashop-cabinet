import { useEffect, useRef, useState } from "react";
import { Save, CheckCircle2, RotateCcw, Palette, ImagePlus, Trash2 } from "lucide-react";
import { appearanceAdminApi, type AdminAppearance } from "@/api/appearance";
import { useBranding, applyAccent, applyBackground } from "@/contexts/BrandingContext";
import { useTheme } from "@/contexts/ThemeContext";
import { normalizeHex } from "@/lib/color";
import { ApiError } from "@/types/api";

const ACCENT_PRESETS = ["#4d8bff", "#7c5cff", "#ec4899", "#f43f5e", "#f59e0b", "#10b981", "#06b6d4"];
const DARK_BG_PRESETS = ["#0a0d15", "#101522", "#13111c", "#0e1512", "#111111"];
const LIGHT_BG_PRESETS = ["#f6f4ee", "#ffffff", "#f1f5f9", "#fafaf9", "#eef2f7"];

function ColorField({
  label,
  hint,
  value,
  presets,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string | null;
  presets: string[];
  onChange: (v: string | null) => void;
}) {
  const current = normalizeHex(value);
  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-fg">{label}</h2>
          {hint && <p className="mt-0.5 text-xs text-fg-muted">{hint}</p>}
        </div>
        {current && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="inline-flex items-center gap-1 rounded-lg border border-border-subtle bg-bg px-2.5 py-1 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Из темы
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {presets.map((p) => (
          <button
            key={p}
            type="button"
            aria-label={p}
            onClick={() => onChange(p)}
            className="h-8 w-8 rounded-lg border-2 transition-transform hover:scale-110"
            style={{
              background: p,
              borderColor: current === normalizeHex(p) ? "var(--fg)" : "var(--border)",
            }}
          />
        ))}

        <label className="ml-1 inline-flex h-8 cursor-pointer items-center gap-2 rounded-lg border border-border-subtle bg-bg px-2.5 text-xs text-fg-muted">
          <input
            type="color"
            value={current || "#4d8bff"}
            onChange={(e) => onChange(e.target.value)}
            className="h-5 w-5 cursor-pointer rounded border-0 bg-transparent p-0"
          />
          Свой цвет
        </label>

        <span className="tabular text-xs text-fg-subtle">{current || "по умолчанию"}</span>
      </div>
    </section>
  );
}

export default function AdminAppearancePage() {
  const { refresh } = useBranding();
  const { resolved } = useTheme();  // активная тема для предпросмотра фона
  const [form, setForm] = useState<AdminAppearance | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logoBusy, setLogoBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    appearanceAdminApi
      .get()
      .then(setForm)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка загрузки"))
      .finally(() => setLoading(false));
  }, []);

  // Живой предпросмотр: применяем цвета сразу. Фон — по активной теме
  // (переключи тему, чтобы увидеть фон другой темы).
  useEffect(() => {
    if (!form) return;
    applyAccent(form.accent);
    const themed = resolved === "dark" ? form.background_dark : form.background_light;
    applyBackground(themed ?? form.background);
  }, [form?.accent, form?.background, form?.background_dark, form?.background_light, resolved]);

  // При уходе со страницы восстанавливаем реально сохранённое оформление
  // (на случай несохранённого предпросмотра).
  useEffect(() => {
    return () => {
      refresh();
    };
  }, [refresh]);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      await appearanceAdminApi.update({
        brand_name: form.brand_name ?? "", // пусто → авто-подхват из конфигурации
        accent: form.accent ?? "",
        background: form.background ?? "",
        background_dark: form.background_dark ?? "",
        background_light: form.background_light ?? "",
      });
      await refresh(); // перечитать и применить во всём кабинете
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  const onPickLogo = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // позволяем выбрать тот же файл повторно
    if (!file || !form) return;
    setLogoBusy(true);
    setError(null);
    try {
      const { logo_url } = await appearanceAdminApi.uploadLogo(file);
      setForm({ ...form, logo_url });
      await refresh(); // применить логотип во всём кабинете сразу
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось загрузить логотип");
    } finally {
      setLogoBusy(false);
    }
  };

  const removeLogo = async () => {
    if (!form) return;
    setLogoBusy(true);
    setError(null);
    try {
      await appearanceAdminApi.deleteLogo();
      setForm({ ...form, logo_url: null });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось удалить логотип");
    } finally {
      setLogoBusy(false);
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  if (!form) return null;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Sticky header */}
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-border-subtle bg-bg/80 px-5 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <h1 className="flex items-center gap-2 text-xl font-bold text-fg md:text-2xl">
          <Palette className="h-5 w-5 text-accent" />
          Оформление
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

      {error && <p className="text-sm text-danger">{error}</p>}

      {/* Название сервиса */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="text-sm font-semibold text-fg">Название сервиса</h2>
        <p className="mt-0.5 text-xs text-fg-muted">
          Отображается в шапке кабинета, на экране входа и в заголовке вкладки.
          Оставьте пустым — подхватится автоматически (имя бота):{" "}
          <span className="text-fg">{form.brand_name_resolved}</span>.
        </p>
        <input
          type="text"
          maxLength={40}
          value={form.brand_name ?? ""}
          onChange={(e) => setForm({ ...form, brand_name: e.target.value })}
          placeholder={`Авто: ${form.brand_name_resolved}`}
          className="input mt-3"
        />
      </section>

      {/* Логотип */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="text-sm font-semibold text-fg">Логотип</h2>
        <p className="mt-0.5 text-xs text-fg-muted">
          Значок рядом с названием на входе и в меню. PNG, JPG, WEBP, SVG или GIF до 2 МБ,
          лучше квадратный. Без логотипа показывается иконка по умолчанию.
        </p>

        <div className="mt-4 flex items-center gap-4">
          <div className="flex h-16 w-16 items-center justify-center overflow-hidden rounded-2xl border border-border-subtle bg-bg">
            {form.logo_url ? (
              <img src={form.logo_url} alt="" className="h-full w-full object-contain" />
            ) : (
              <ImagePlus className="h-6 w-6 text-fg-subtle" />
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp,.svg,.gif,image/*"
              className="hidden"
              onChange={onPickLogo}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={logoBusy}
              className="inline-flex items-center gap-2 rounded-xl border border-border-subtle bg-bg px-3.5 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay disabled:opacity-60"
            >
              <ImagePlus className="h-4 w-4" />
              {logoBusy ? "Загрузка…" : form.logo_url ? "Заменить" : "Загрузить"}
            </button>
            {form.logo_url && (
              <button
                type="button"
                onClick={removeLogo}
                disabled={logoBusy}
                className="inline-flex items-center gap-2 rounded-xl border border-danger/30 bg-danger/5 px-3.5 py-2 text-sm font-medium text-danger transition-colors hover:bg-danger/10 disabled:opacity-60"
              >
                <Trash2 className="h-4 w-4" />
                Удалить
              </button>
            )}
          </div>
        </div>
      </section>

      <ColorField
        label="Акцентный цвет"
        hint="Кнопки, ссылки, выделения и свечения во всём кабинете."
        value={form.accent}
        presets={ACCENT_PRESETS}
        onChange={(v) => setForm({ ...form, accent: v })}
      />

      <ColorField
        label="Фон — тёмная тема"
        hint="Применяется, когда у пользователя включена тёмная тема. Оттенки поверхностей и текст подбираются автоматически."
        value={form.background_dark}
        presets={DARK_BG_PRESETS}
        onChange={(v) => setForm({ ...form, background_dark: v })}
      />

      <ColorField
        label="Фон — светлая тема"
        hint="Применяется при светлой теме. Переключите тему кабинета, чтобы увидеть предпросмотр этого фона."
        value={form.background_light}
        presets={LIGHT_BG_PRESETS}
        onChange={(v) => setForm({ ...form, background_light: v })}
      />

      {/* Превью */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="mb-3 text-sm font-semibold text-fg">Предпросмотр</h2>
        <div className="surface flex flex-col gap-3 p-5">
          <span className="brand-wordmark text-xl font-bold tracking-tight">
            {form.brand_name || form.brand_name_resolved}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <span className="btn-gradient inline-flex h-9 items-center rounded-xl px-4 text-sm font-semibold text-white">
              Кнопка
            </span>
            <span className="inline-flex items-center gap-1 rounded-full border border-accent/30 bg-accent-subtle px-2.5 py-0.5 text-xs font-medium text-accent">
              ★ Бейдж
            </span>
            <a className="text-sm font-medium text-accent" href="#" onClick={(e) => e.preventDefault()}>
              Ссылка
            </a>
          </div>
        </div>
        <p className="mt-2 text-xs text-fg-subtle">
          Изменения применяются в кабинете сразу после «Сохранить».
        </p>
      </section>
    </div>
  );
}
