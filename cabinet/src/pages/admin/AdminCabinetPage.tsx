import { useEffect, useState } from "react";
import { Save, CheckCircle2, DoorOpen, AlertCircle } from "lucide-react";
import { appearanceAdminApi, type AdminAppearance } from "@/api/appearance";
import { useBranding } from "@/contexts/BrandingContext";
import { ApiError } from "@/types/api";
import { Flag } from "@/components/Flag";
import { LANGUAGES } from "@/i18n/config";
import { useT } from "@/i18n/I18nContext";

const ALL_LANG_CODES = LANGUAGES.map((l) => l.code);

/**
 * Админ: доступ к кабинету и языки. Вынесено из «Оформления» (там только бренд/цвета/
 * логотип) — эти настройки про поведение/доступ, а не про внешний вид. Данные всё те же
 * (branding.json через appearance API), меняется только место в меню.
 */
export default function AdminCabinetPage() {
  const t = useT();
  const { refresh, can } = useBranding();
  const [form, setForm] = useState<AdminAppearance | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    appearanceAdminApi
      .get()
      .then(setForm)
      .catch(() => setError(t("adm.cabinet.load_error")))
      .finally(() => setLoading(false));
    // перевод берём на момент загрузки; перезапрашивать при смене языка не нужно
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      // enabled_languages шлём ТОЛЬКО если это строгое подмножество. Иначе (выбраны все /
      // не тронуто) поле опускаем → бэкенд сохраняет null = «все, включая будущие языки».
      // Раньше безусловный ALL_LANG_CODES морозил снимок → добавленный позже язык оказывался
      // выключен, хотя админ его не отключал.
      const langs = form.enabled_languages;
      const isRestricted =
        !!langs && langs.length > 0 && langs.length < ALL_LANG_CODES.length;
      await appearanceAdminApi.update({
        sub_link_enabled: form.sub_link_enabled !== false,
        crypto_links_enabled: form.crypto_links_enabled === true,
        maintenance_enabled: form.maintenance_enabled === true,
        maintenance_follow_bot: form.maintenance_follow_bot === true,
        maintenance_message: form.maintenance_message ?? "",
        maintenance_block_login: form.maintenance_block_login !== false,
        maintenance_block_registration: form.maintenance_block_registration !== false,
        maintenance_block_payments: form.maintenance_block_payments !== false,
        ...(isRestricted ? { enabled_languages: langs } : {}),
        // Поверх чужого бэкенда блока нет (возможность выключена) — и поле не шлём.
        ...(can("device_upsell") ? { device_upsell_enabled: form.device_upsell_enabled !== false } : {}),
      });
      await refresh();
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.cabinet.save_error"));
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
  if (!form) return <p className="text-sm text-danger">{error ?? t("adm.cabinet.error")}</p>;

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
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.cabinet.title")}</h1>
      </div>

      {/* Прямая ссылка подписки + тех-работы */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="mb-3 text-sm font-semibold text-fg">{t("adm.cabinet.access_title")}</h2>

        <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
          <input
            type="checkbox"
            checked={form.sub_link_enabled !== false}
            onChange={(e) => setForm({ ...form, sub_link_enabled: e.target.checked })}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          {t("adm.cabinet.sub_link")}
        </label>
        <p className="ml-6 text-xs text-fg-subtle">{t("adm.cabinet.sub_link_hint")}</p>

        <label className="mt-3 flex items-center gap-2.5 py-1 text-sm text-fg">
          <input
            type="checkbox"
            checked={form.crypto_links_enabled === true}
            onChange={(e) => setForm({ ...form, crypto_links_enabled: e.target.checked })}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          {t("adm.cabinet.crypto_links")}
        </label>
        <p className="ml-6 text-xs text-fg-subtle">{t("adm.cabinet.crypto_links_hint")}</p>

        {can("device_upsell") && (
          <>
            <label className="mt-3 flex items-center gap-2.5 py-1 text-sm text-fg">
              <input
                type="checkbox"
                checked={form.device_upsell_enabled !== false}
                onChange={(e) => setForm({ ...form, device_upsell_enabled: e.target.checked })}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              {t("adm.cabinet.device_upsell")}
            </label>
            <p className="ml-6 text-xs text-fg-subtle">{t("adm.cabinet.device_upsell_hint")}</p>
          </>
        )}

        <div className="mt-4 border-t border-border-subtle pt-4">
          <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
            <input
              type="checkbox"
              checked={form.maintenance_enabled === true}
              onChange={(e) => setForm({ ...form, maintenance_enabled: e.target.checked })}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            {t("adm.cabinet.maintenance")}
          </label>
          <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
            <input
              type="checkbox"
              checked={form.maintenance_follow_bot === true}
              onChange={(e) => setForm({ ...form, maintenance_follow_bot: e.target.checked })}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            {t("adm.cabinet.maintenance_follow_bot")}
          </label>
          <p className="ml-6 mb-2.5 mt-1 text-xs text-fg-subtle">
            {t("adm.cabinet.maintenance_hint")}
          </p>

          {(form.maintenance_enabled === true || form.maintenance_follow_bot === true) && (
            <div className="mb-3 ml-6 rounded-xl border border-border-subtle bg-bg px-3.5 py-3">
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-fg-subtle">
                {t("adm.cabinet.restrict_title")}
              </p>
              <label className="flex items-center gap-2.5 py-1 text-sm text-fg">
                <input
                  type="checkbox"
                  checked={form.maintenance_block_login !== false}
                  onChange={(e) => setForm({ ...form, maintenance_block_login: e.target.checked })}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                {t("adm.cabinet.block_login")}
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
                {t("adm.cabinet.block_registration")}
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
                {t("adm.cabinet.block_payments")}
              </label>
              <p className="mt-1.5 text-xs text-fg-subtle">{t("adm.cabinet.restrict_hint")}</p>
            </div>
          )}

          <input
            value={form.maintenance_message ?? ""}
            onChange={(e) => setForm({ ...form, maintenance_message: e.target.value })}
            placeholder={t("adm.cabinet.maintenance_message_ph")}
            className="w-full rounded-lg border border-border-subtle bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent"
          />
        </div>
      </section>

      {/* Языки кабинета */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="mb-1 text-sm font-semibold text-fg">{t("adm.cabinet.langs_title")}</h2>
        <p className="mb-3 text-xs text-fg-subtle">{t("adm.cabinet.langs_hint")}</p>
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
          {saved ? t("adm.cabinet.saved") : saving ? t("adm.cabinet.saving") : t("adm.cabinet.save")}
        </button>
      </div>
    </div>
  );
}
