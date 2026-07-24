import { useEffect, useState } from "react";
import { Save, CheckCircle2, DoorOpen, AlertCircle } from "lucide-react";
import { appearanceAdminApi, type AdminAppearance } from "@/api/appearance";
import { useBranding } from "@/contexts/BrandingContext";
import { ApiError } from "@/types/api";
import { Flag } from "@/components/Flag";
import { LANGUAGES } from "@/i18n/config";

const ALL_LANG_CODES = LANGUAGES.map((l) => l.code);

/**
 * Админ: доступ к кабинету и языки. Вынесено из «Оформления» (там только бренд/цвета/
 * логотип) — эти настройки про поведение/доступ, а не про внешний вид. Данные всё те же
 * (branding.json через appearance API), меняется только место в меню.
 */
export default function AdminCabinetPage() {
  const { refresh } = useBranding();
  const [form, setForm] = useState<AdminAppearance | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    appearanceAdminApi
      .get()
      .then(setForm)
      .catch(() => setError("Не удалось загрузить"))
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      await appearanceAdminApi.update({
        sub_link_enabled: form.sub_link_enabled !== false,
        maintenance_enabled: form.maintenance_enabled === true,
        maintenance_follow_bot: form.maintenance_follow_bot === true,
        maintenance_message: form.maintenance_message ?? "",
        maintenance_block_login: form.maintenance_block_login !== false,
        maintenance_block_registration: form.maintenance_block_registration !== false,
        maintenance_block_payments: form.maintenance_block_payments !== false,
        enabled_languages:
          form.enabled_languages && form.enabled_languages.length
            ? form.enabled_languages
            : ALL_LANG_CODES,
      });
      await refresh();
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  if (!form) return <p className="text-sm text-danger">{error ?? "Ошибка"}</p>;

  const langActive = (code: string) => {
    const list = form.enabled_languages;
    return !list || list.length === 0 ? true : list.includes(code);
  };
  const toggleLang = (code: string, on: boolean) => {
    const base =
      form.enabled_languages && form.enabled_languages.length
        ? new Set(form.enabled_languages)
        : new Set(ALL_LANG_CODES);
    if (on) base.add(code);
    else base.delete(code);
    base.add("ru");
    setForm({ ...form, enabled_languages: ALL_LANG_CODES.filter((c) => base.has(c)) });
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <DoorOpen className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Доступ и язык</h1>
      </div>

      {/* Прямая ссылка подписки + тех-работы */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="mb-3 text-sm font-semibold text-fg">Кабинет и доступ</h2>

        <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
          <input
            type="checkbox"
            checked={form.sub_link_enabled !== false}
            onChange={(e) => setForm({ ...form, sub_link_enabled: e.target.checked })}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          Показывать прямую ссылку подписки и QR
        </label>
        <p className="ml-6 text-xs text-fg-subtle">
          Выключите, чтобы скрыть блок «Прямая ссылка подписки» и QR в разделе подключения.
        </p>

        <div className="mt-4 border-t border-border-subtle pt-4">
          <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
            <input
              type="checkbox"
              checked={form.maintenance_enabled === true}
              onChange={(e) => setForm({ ...form, maintenance_enabled: e.target.checked })}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            Режим тех-работ (кабинет закрыт для пользователей)
          </label>
          <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
            <input
              type="checkbox"
              checked={form.maintenance_follow_bot === true}
              onChange={(e) => setForm({ ...form, maintenance_follow_bot: e.target.checked })}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            Следовать за режимом доступа бота («Запрещён для всех» → кабинет тоже закрыт)
          </label>
          <p className="ml-6 mb-2.5 mt-1 text-xs text-fg-subtle">
            Админы заходят всегда; экран входа остаётся доступным.
          </p>

          {(form.maintenance_enabled === true || form.maintenance_follow_bot === true) && (
            <div className="mb-3 ml-6 rounded-xl border border-border-subtle bg-bg px-3.5 py-3">
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-fg-subtle">
                Что ограничивать
              </p>
              <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
                <input
                  type="checkbox"
                  checked={form.maintenance_block_login !== false}
                  onChange={(e) => setForm({ ...form, maintenance_block_login: e.target.checked })}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                Вход в кабинет (полностью закрыт для не-админов)
              </label>
              <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
                <input
                  type="checkbox"
                  checked={form.maintenance_block_registration !== false}
                  onChange={(e) =>
                    setForm({ ...form, maintenance_block_registration: e.target.checked })
                  }
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                Новые регистрации
              </label>
              <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
                <input
                  type="checkbox"
                  checked={form.maintenance_block_payments !== false}
                  onChange={(e) =>
                    setForm({ ...form, maintenance_block_payments: e.target.checked })
                  }
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                Оплата и пополнение баланса
              </label>
              <p className="mt-1.5 text-xs text-fg-subtle">
                Снимите «Вход», чтобы кабинет оставался открытым, а ограничить только регистрацию
                и/или оплату.
              </p>
            </div>
          )}

          <input
            value={form.maintenance_message ?? ""}
            onChange={(e) => setForm({ ...form, maintenance_message: e.target.value })}
            placeholder="Текст на экране тех-работ (необязательно)"
            className="w-full rounded-lg border border-border-subtle bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent"
          />
        </div>
      </section>

      {/* Языки кабинета */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="mb-1 text-sm font-semibold text-fg">Языки кабинета</h2>
        <p className="mb-3 text-xs text-fg-subtle">
          Снимите галку, чтобы убрать язык из выбора. Русский отключить нельзя. Если пользователь
          выбрал отключённый язык — кабинет вернёт его на русский.
        </p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
          {LANGUAGES.map((l) => {
            const isRu = l.code === "ru";
            return (
              <label
                key={l.code}
                className={`flex items-center gap-2.5 py-1 text-sm ${isRu ? "text-fg-muted" : "text-fg"}`}
              >
                <input
                  type="checkbox"
                  checked={isRu ? true : langActive(l.code)}
                  disabled={isRu}
                  onChange={(e) => toggleLang(l.code, e.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)] disabled:opacity-60"
                />
                <Flag code={l.country} className="h-3.5 w-5" />
                <span className="truncate">{l.label}</span>
              </label>
            );
          })}
        </div>
      </section>

      {error && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircle className="h-4 w-4" /> {error}
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-accent/90 disabled:opacity-60"
        >
          {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? "Сохранено" : saving ? "Сохранение…" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
