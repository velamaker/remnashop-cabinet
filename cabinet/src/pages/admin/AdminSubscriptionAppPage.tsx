import { useEffect, useState } from "react";
import {
  Smartphone,
  Save,
  AlertCircle,
  CheckCircle2,
  Plus,
  Trash2,
  ExternalLink,
  Palette,
} from "lucide-react";
import { subscriptionAppAdminApi, type SubscriptionAppSettings } from "@/api/admin";
import { useBranding } from "@/contexts/BrandingContext";
import { buildHappTheme, cabinetColors } from "@/lib/happTheme";
import { useI18n, useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { pluralFor } from "@/lib/pluralRu";

// Заголовки, под которые в форме есть отдельные поля. Остальное показываем
// как произвольные пары ключ/значение (providerId, hide-settings и т.п.).
const SUB_INFO_TEXT = "sub-info-text";
const SUB_INFO_COLOR = "sub-info-color";
const SUB_INFO_BTN_TEXT = "sub-info-button-text";
const SUB_INFO_BTN_LINK = "sub-info-button-link";
const SUB_EXPIRE = "sub-expire";
const SUB_EXPIRE_LINK = "sub-expire-button-link";
const COLOR_PROFILE = "color-profile";

const NAMED_KEYS = [
  SUB_INFO_TEXT,
  SUB_INFO_COLOR,
  SUB_INFO_BTN_TEXT,
  SUB_INFO_BTN_LINK,
  SUB_EXPIRE,
  SUB_EXPIRE_LINK,
  COLOR_PROFILE,
];

// В константе — КЛЮЧИ перевода: текст берётся в рендере, иначе подпись
// застынет на языке первого рендера.
const INFO_COLORS: { value: string; labelKey: string }[] = [
  { value: "blue", labelKey: "adm.subapp.color_blue" },
  { value: "green", labelKey: "adm.subapp.color_green" },
  { value: "red", labelKey: "adm.subapp.color_red" },
];

// Одна фраза — один ключ: чип с deep-link <code>…</code> живёт ВНУТРИ перевода,
// чтобы предложение не собиралось из кусков.
function withMarkup(s: string) {
  return s
    .split(/(<code>.*?<\/code>)/g)
    .map((part, i) =>
      part.startsWith("<code>") ? (
        <code key={i} className="font-mono">
          {part.slice(6, -7)}
        </code>
      ) : (
        part
      ),
    );
}

function Switch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg ${
        checked ? "bg-accent" : "bg-border"
      }`}
    >
      <span
        className={`inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
          checked ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

function Field({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-fg">{label}</label>
      {sub && <p className="text-xs text-fg-muted">{sub}</p>}
      {children}
    </div>
  );
}

const inputCls =
  "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg outline-none transition-colors focus:border-accent";

// Раздел «Подписка в приложении»: настройки панели Remnawave, которые Happ читает
// при импорте ссылки — брендинг, плашки, тема оформления и маршрутизация.
export default function AdminSubscriptionAppPage() {
  const t = useT();
  const { lang } = useI18n();
  const { brandName } = useBranding();
  const [cfg, setCfg] = useState<SubscriptionAppSettings | null>(null);
  const [headers, setHeaders] = useState<Record<string, string>>({});
  const [extra, setExtra] = useState<[string, string][]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    subscriptionAppAdminApi
      .get()
      .then((data) => {
        setCfg(data);
        const all = data.custom_response_headers ?? {};
        setHeaders(all);
        setExtra(Object.entries(all).filter(([k]) => !NAMED_KEYS.includes(k)));
      })
      .catch(() => setError(translate("adm.subapp.load_error")))
      .finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<SubscriptionAppSettings>) => {
    setCfg((c) => (c ? { ...c, ...p } : c));
    setSaved(false);
  };

  // Пустое значение = заголовок не отдаём вовсе.
  const setHeader = (key: string, value: string) => {
    setHeaders((h) => {
      const next = { ...h };
      if (value) next[key] = value;
      else delete next[key];
      return next;
    });
    setSaved(false);
  };

  const generateTheme = () => {
    const { accent, accent2, bg } = cabinetColors();
    setHeader(COLOR_PROFILE, buildHappTheme(accent, accent2, bg));
  };

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);

    const merged: Record<string, string> = { ...headers };
    for (const [k, v] of extra) if (k.trim()) merged[k.trim()] = v;
    for (const k of Object.keys(merged)) {
      if (!NAMED_KEYS.includes(k) && !extra.some(([ek]) => ek.trim() === k)) delete merged[k];
    }

    try {
      const next = await subscriptionAppAdminApi.update({
        profile_title: cfg.profile_title ?? "",
        support_link: cfg.support_link ?? "",
        profile_update_interval: cfg.profile_update_interval ?? 12,
        is_profile_webpage_url_enabled: cfg.is_profile_webpage_url_enabled ?? false,
        happ_announce: cfg.happ_announce ?? "",
        happ_routing: cfg.happ_routing ?? "",
        custom_response_headers: Object.keys(merged).length ? merged : null,
      });
      setCfg(next);
      const all = next.custom_response_headers ?? {};
      setHeaders(all);
      setExtra(Object.entries(all).filter(([k]) => !NAMED_KEYS.includes(k)));
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("adm.subapp.save_error"));
    } finally {
      setSaving(false);
    }
  };

  const announceLeft = (cfg?.limits.announce ?? 200) - (cfg?.happ_announce?.length ?? 0);
  const themeValue = headers[COLOR_PROFILE] ?? "";
  const expireOn = ["true", "1"].includes((headers[SUB_EXPIRE] ?? "").toLowerCase());

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Smartphone className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.subapp.title")}</h1>
      </div>

      <p className="px-1 text-xs text-fg-muted">{t("adm.subapp.intro")}</p>

      {loading ? (
        <p className="px-1 text-sm text-fg-muted">{t("adm.subapp.loading")}</p>
      ) : !cfg ? (
        <p className="px-1 text-sm text-danger">{error ?? t("adm.subapp.error")}</p>
      ) : (
        <>
          <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
            <h3 className="text-sm font-semibold text-fg">{t("adm.subapp.branding")}</h3>

            {/* «1 символ / 2 символа / 5 символов»: форму выбирает pluralFor ПО ЯЗЫКУ. */}
            <Field
              label={t("adm.subapp.service_name")}
              sub={t(
                pluralFor(
                  lang,
                  cfg.limits.title,
                  "adm.subapp.service_name_hint_one",
                  "adm.subapp.service_name_hint_few",
                  "adm.subapp.service_name_hint_many",
                ),
                { n: cfg.limits.title },
              )}
            >
              <div className="flex gap-2">
                <input
                  className={inputCls}
                  maxLength={cfg.limits.title}
                  value={cfg.profile_title ?? ""}
                  onChange={(e) => patch({ profile_title: e.target.value })}
                  placeholder={brandName}
                />
                {cfg.profile_title !== brandName && (
                  <button
                    type="button"
                    onClick={() => patch({ profile_title: brandName.slice(0, cfg.limits.title) })}
                    className="shrink-0 rounded-xl border border-border-subtle px-3 text-xs text-fg-muted transition-colors hover:text-fg"
                  >
                    {t("adm.subapp.use_brand")}
                  </button>
                )}
              </div>
            </Field>

            <Field label={t("adm.subapp.support_link")} sub={t("adm.subapp.support_link_hint")}>
              <input
                className={inputCls}
                value={cfg.support_link ?? ""}
                onChange={(e) => patch({ support_link: e.target.value })}
                placeholder="https://t.me/your_support"
              />
            </Field>

            <Field
              label={t("adm.subapp.announce")}
              sub={t(
                pluralFor(
                  lang,
                  announceLeft,
                  "adm.subapp.announce_hint_one",
                  "adm.subapp.announce_hint_few",
                  "adm.subapp.announce_hint_many",
                ),
                { n: announceLeft },
              )}
            >
              <textarea
                className={`${inputCls} min-h-[72px] resize-y`}
                maxLength={cfg.limits.announce}
                value={cfg.happ_announce ?? ""}
                onChange={(e) => patch({ happ_announce: e.target.value })}
                placeholder={t("adm.subapp.announce_placeholder", { brand: brandName })}
              />
            </Field>

            <div className="flex items-center justify-between gap-4 rounded-xl bg-bg px-4 py-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-fg">{t("adm.subapp.site_button")}</p>
                <p className="mt-0.5 text-xs text-fg-muted">{t("adm.subapp.site_button_hint")}</p>
              </div>
              <Switch
                checked={!!cfg.is_profile_webpage_url_enabled}
                onChange={(v) => patch({ is_profile_webpage_url_enabled: v })}
              />
            </div>

            <Field label={t("adm.subapp.update_interval")} sub={t("adm.subapp.update_interval_hint")}>
              <input
                type="number"
                min={1}
                max={168}
                className={`${inputCls} max-w-[140px]`}
                value={cfg.profile_update_interval ?? 12}
                onChange={(e) => patch({ profile_update_interval: Number(e.target.value) })}
              />
            </Field>
          </section>

          <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
            <div>
              <h3 className="text-sm font-semibold text-fg">{t("adm.subapp.banner")}</h3>
              <p className="mt-0.5 text-xs text-fg-muted">{t("adm.subapp.banner_hint")}</p>
            </div>

            <Field label={t("adm.subapp.banner_text")} sub={t("adm.subapp.banner_text_hint")}>
              <input
                className={inputCls}
                maxLength={200}
                value={headers[SUB_INFO_TEXT] ?? ""}
                onChange={(e) => setHeader(SUB_INFO_TEXT, e.target.value)}
                placeholder={t("adm.subapp.banner_text_placeholder")}
              />
            </Field>

            <div className="grid gap-4 md:grid-cols-2">
              <Field label={t("adm.subapp.banner_color")}>
                <select
                  className={inputCls}
                  value={headers[SUB_INFO_COLOR] ?? "blue"}
                  onChange={(e) => setHeader(SUB_INFO_COLOR, e.target.value)}
                >
                  {INFO_COLORS.map((c) => (
                    <option key={c.value} value={c.value}>
                      {t(c.labelKey)}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label={t("adm.subapp.banner_btn_text")} sub={t("adm.subapp.banner_btn_text_hint")}>
                <input
                  className={inputCls}
                  maxLength={25}
                  value={headers[SUB_INFO_BTN_TEXT] ?? ""}
                  onChange={(e) => setHeader(SUB_INFO_BTN_TEXT, e.target.value)}
                  placeholder={t("adm.subapp.banner_btn_text_placeholder")}
                />
              </Field>
            </div>

            <Field label={t("adm.subapp.banner_btn_link")}>
              <input
                className={inputCls}
                value={headers[SUB_INFO_BTN_LINK] ?? ""}
                onChange={(e) => setHeader(SUB_INFO_BTN_LINK, e.target.value)}
                placeholder="https://cabinet.example.com/billing"
              />
            </Field>
          </section>

          <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
            <div>
              <h3 className="text-sm font-semibold text-fg">{t("adm.subapp.expire")}</h3>
              <p className="mt-0.5 text-xs text-fg-muted">{t("adm.subapp.expire_hint")}</p>
            </div>

            <div className="flex items-center justify-between gap-4 rounded-xl bg-bg px-4 py-3">
              <p className="text-sm font-medium text-fg">{t("adm.subapp.expire_toggle")}</p>
              <Switch checked={expireOn} onChange={(v) => setHeader(SUB_EXPIRE, v ? "true" : "")} />
            </div>

            <Field label={t("adm.subapp.expire_link")} sub={t("adm.subapp.expire_link_hint")}>
              <input
                className={inputCls}
                value={headers[SUB_EXPIRE_LINK] ?? ""}
                onChange={(e) => setHeader(SUB_EXPIRE_LINK, e.target.value)}
                placeholder="https://cabinet.example.com/billing"
              />
            </Field>
          </section>

          <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
            <div>
              <h3 className="text-sm font-semibold text-fg">{t("adm.subapp.theme")}</h3>
              <p className="mt-0.5 text-xs text-fg-muted">{t("adm.subapp.theme_hint")}</p>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={generateTheme}
                className="inline-flex items-center gap-1.5 rounded-xl bg-accent px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-accent/90"
              >
                <Palette className="h-4 w-4" /> {t("adm.subapp.theme_build")}
              </button>
              <button
                type="button"
                onClick={() => setHeader(COLOR_PROFILE, "resetcolors")}
                className="rounded-xl border border-border-subtle px-3 py-2 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
              >
                {t("adm.subapp.theme_reset")}
              </button>
              {themeValue && (
                <button
                  type="button"
                  onClick={() => setHeader(COLOR_PROFILE, "")}
                  className="rounded-xl border border-border-subtle px-3 py-2 text-xs font-medium text-fg-muted transition-colors hover:text-danger"
                >
                  {t("adm.subapp.theme_clear")}
                </button>
              )}
            </div>

            <Field label={t("adm.subapp.theme_json")} sub={t("adm.subapp.theme_json_hint")}>
              <textarea
                className={`${inputCls} min-h-[120px] resize-y font-mono text-xs`}
                value={themeValue}
                onChange={(e) => setHeader(COLOR_PROFILE, e.target.value)}
                placeholder='{"buttonColor": "#4D8BFFFF", …}'
              />
            </Field>
          </section>

          <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
            <div>
              <h3 className="text-sm font-semibold text-fg">{t("adm.subapp.routing")}</h3>
              <p className="mt-0.5 text-xs text-fg-muted">{withMarkup(t("adm.subapp.routing_hint"))}</p>
            </div>

            <Field label={t("adm.subapp.routing_config")}>
              <textarea
                className={`${inputCls} min-h-[72px] resize-y break-all font-mono text-xs`}
                value={cfg.happ_routing ?? ""}
                onChange={(e) => patch({ happ_routing: e.target.value })}
                placeholder="happ://routing/onadd/eyJOYW1lIjoi…"
              />
            </Field>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={async () => {
                  const { routing } = await subscriptionAppAdminApi.defaultRouting();
                  patch({ happ_routing: routing });
                }}
                className="rounded-xl bg-accent px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-accent/90"
              >
                {t("adm.subapp.routing_default")}
              </button>
              {cfg.happ_routing && (
                <button
                  type="button"
                  onClick={() => patch({ happ_routing: "" })}
                  className="rounded-xl border border-border-subtle px-3 py-2 text-xs font-medium text-fg-muted transition-colors hover:text-danger"
                >
                  {t("adm.subapp.routing_clear")}
                </button>
              )}
            </div>

            <a
              href="https://utils.docs.rw/happ-rb"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
            >
              {t("adm.subapp.routing_builder")}
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </section>

          <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
            <div>
              <h3 className="text-sm font-semibold text-fg">{t("adm.subapp.headers")}</h3>
              <p className="mt-0.5 text-xs text-fg-muted">{withMarkup(t("adm.subapp.headers_hint"))}</p>
            </div>

            <div className="space-y-2">
              {extra.map(([k, v], i) => (
                <div key={i} className="flex gap-2">
                  <input
                    className={`${inputCls} md:max-w-[220px]`}
                    value={k}
                    onChange={(e) =>
                      setExtra((h) => h.map((row, j) => (j === i ? [e.target.value, row[1]] : row)))
                    }
                    placeholder="providerId"
                  />
                  <input
                    className={inputCls}
                    value={v}
                    onChange={(e) =>
                      setExtra((h) => h.map((row, j) => (j === i ? [row[0], e.target.value] : row)))
                    }
                    placeholder={t("adm.subapp.header_value_placeholder")}
                  />
                  <button
                    type="button"
                    onClick={() => setExtra((h) => h.filter((_, j) => j !== i))}
                    className="shrink-0 rounded-xl border border-border-subtle px-3 text-fg-muted transition-colors hover:text-danger"
                    aria-label={t("adm.subapp.header_delete")}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}

              <button
                type="button"
                onClick={() => setExtra((h) => [...h, ["", ""]])}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border-subtle px-3 py-2 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
              >
                <Plus className="h-4 w-4" /> {t("adm.subapp.header_add")}
              </button>
            </div>
          </section>

          {error && (
            <div className="flex items-center gap-2 px-1 text-sm text-danger">
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
              {saved ? t("adm.subapp.saved") : saving ? t("adm.subapp.saving") : t("adm.subapp.save")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
