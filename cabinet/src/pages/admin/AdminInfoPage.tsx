import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Save, Languages, ClipboardCopy } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { infoAdminApi, type AdminInfoResponse, type InfoContent } from "@/api/info";
import { useBranding } from "@/contexts/BrandingContext";
import { LANGUAGES } from "@/i18n/config";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { botHas } from "@/lib/botCapabilities";
import { ApiError } from "@/types/api";

// Подписи вкладок — КЛЮЧИ, а не готовый текст: список модульный, а переводится
// он в компоненте, где известен язык.
const SECTIONS = [
  { id: "faq", labelKey: "adm.info.tab_faq" },
  { id: "rules", labelKey: "adm.info.tab_rules" },
  { id: "privacy", labelKey: "adm.info.tab_privacy" },
  { id: "offer", labelKey: "adm.info.tab_offer" },
  { id: "statuses", labelKey: "adm.info.tab_statuses" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];
type TextSection = Exclude<SectionId, "faq">;

const BASE_LANG = "ru";

const textInput =
  "w-full rounded-lg border border-[var(--border)] bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent";

// Одна фраза — один ключ: чипы с примерами markdown <code>…</code> живут ВНУТРИ
// перевода. Так переводчик сам решает порядок слов, а предложение не собирается
// из кусков.
function withMarkup(s: string) {
  return s
    .split(/(<code>.*?<\/code>)/g)
    .map((part, i) => (part.startsWith("<code>") ? <code key={i}>{part.slice(6, -7)}</code> : part));
}

/** Пустой перевод: показываем ровно то, что сохранено, без русского фолбэка. */
function ownContent(data: AdminInfoResponse): InfoContent {
  if (data.lang === data.base_lang) {
    return { faq: data.faq, rules: data.rules, privacy: data.privacy, offer: data.offer, statuses: data.statuses };
  }
  return {
    faq: data.own.faq ?? [],
    rules: data.own.rules ?? "",
    privacy: data.own.privacy ?? "",
    offer: data.own.offer ?? "",
    statuses: data.own.statuses ?? "",
  };
}

export default function AdminInfoPage() {
  const t = useT();
  const { appearance } = useBranding();
  // Переводы понимает только бот новее 1.4.6. Со старым бот сохранит присланный
  // текст как РУССКИЙ: вкладки языков там показывать нельзя — одно «Сохранить»
  // подменило бы русскую страницу английской.
  const canTranslate = botHas(appearance, "info_i18n");

  const [lang, setLang] = useState<string>(BASE_LANG);
  const [content, setContent] = useState<InfoContent | null>(null);
  const [base, setBase] = useState<InfoContent | null>(null);
  const [translated, setTranslated] = useState<string[]>([]);
  const [active, setActive] = useState<SectionId>("faq");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const apply = useCallback((data: AdminInfoResponse) => {
    setContent(ownContent(data));
    setBase(data.base ?? null);
    setTranslated(data.translated_langs ?? []);
  }, []);

  useEffect(() => {
    setLoading(true);
    setMsg(null);
    infoAdminApi
      .get(lang)
      .then(apply)
      // translate, а не t: иначе t попал бы в зависимости эффекта и смена языка
      // кабинета перезапрашивала бы контент.
      .catch((e) =>
        setMsg({
          type: "error",
          text: e instanceof ApiError ? e.detail : translate("adm.info.err_generic"),
        }),
      )
      .finally(() => setLoading(false));
  }, [lang, apply]);

  const save = async () => {
    if (!content) return;
    setSaving(true);
    setMsg(null);
    try {
      const saved = await infoAdminApi.update(content, lang);
      apply(saved);
      setMsg({ type: "success", text: t("adm.info.saved") });
    } catch (e) {
      setMsg({ type: "error", text: e instanceof ApiError ? e.detail : t("adm.info.save_error") });
    } finally {
      setSaving(false);
    }
  };

  const setText = (key: TextSection, value: string) =>
    setContent((c) => (c ? { ...c, [key]: value } : c));

  const setFaq = (i: number, field: "q" | "a", value: string) =>
    setContent((c) =>
      c ? { ...c, faq: c.faq.map((it, j) => (j === i ? { ...it, [field]: value } : it)) } : c,
    );

  const addFaq = () => setContent((c) => (c ? { ...c, faq: [...c.faq, { q: "", a: "" }] } : c));

  const removeFaq = (i: number) =>
    setContent((c) => (c ? { ...c, faq: c.faq.filter((_, j) => j !== i) } : c));

  /** Взять русский текст как заготовку перевода — переводить проще, чем писать с нуля. */
  const copyBase = () => {
    if (!base) return;
    if (active === "faq") setContent((c) => (c ? { ...c, faq: base.faq.map((i) => ({ ...i })) } : c));
    else setText(active as TextSection, base[active as TextSection]);
  };

  const isBase = lang === BASE_LANG;
  const sectionEmpty =
    !!content && (active === "faq" ? content.faq.length === 0 : !content[active as TextSection].trim());

  if (loading && !content) {
    return (
      <div className="flex justify-center py-16">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight text-fg">{t("adm.info.title")}</h1>
        <Button onClick={save} isLoading={saving} disabled={!content}>
          <Save className="mr-1.5 h-4 w-4" />
          {t("adm.info.save")}
        </Button>
      </div>
      <p className="text-sm text-fg-muted">{withMarkup(t("adm.info.intro"))}</p>

      {canTranslate && (
        <div className="rounded-xl border border-[var(--border)] bg-bg-subtle p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg">
            <Languages className="h-3.5 w-3.5" />
            {t("adm.info.lang_label")}
          </div>
          <div className="scrollbar-hide flex gap-1.5 overflow-x-auto pb-1">
            {LANGUAGES.map((l) => {
              const done = l.code === BASE_LANG || translated.includes(l.code);
              return (
                <button
                  key={l.code}
                  onClick={() => setLang(l.code)}
                  className={`flex-shrink-0 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    lang === l.code
                      ? "border-accent bg-accent text-accent-fg"
                      : done
                        ? "border-[var(--border)] bg-bg text-fg hover:bg-bg-raised"
                        : "border-dashed border-[var(--border)] bg-bg text-fg-subtle hover:text-fg"
                  }`}
                  title={done ? t("adm.info.translated") : t("adm.info.no_translation")}
                >
                  {l.label}
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-xs text-fg-muted">
            {isBase ? t("adm.info.base_note") : t("adm.info.translation_note")}
          </p>
        </div>
      )}

      {msg && (
        <p
          className={`rounded-lg px-3 py-2 text-sm ${
            msg.type === "success" ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
          }`}
        >
          {msg.text}
        </p>
      )}

      {/* Section tabs */}
      <div className="scrollbar-hide flex gap-2 overflow-x-auto pb-1">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            onClick={() => setActive(s.id)}
            className={`flex-shrink-0 rounded-xl border px-4 py-2 text-sm font-medium transition-colors ${
              active === s.id
                ? "border-accent bg-accent text-accent-fg"
                : "border-[var(--border)] bg-bg-subtle text-fg-muted hover:bg-bg-raised hover:text-fg"
            }`}
          >
            {t(s.labelKey)}
          </button>
        ))}
      </div>

      {!isBase && (
        <div className="flex flex-wrap items-center gap-2">
          {sectionEmpty && (
            <span className="rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs text-amber-600 dark:text-amber-400">
              {t("adm.info.no_translation")}
            </span>
          )}
          <button
            onClick={copyBase}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-bg-subtle px-2.5 py-1 text-xs font-medium text-fg hover:bg-bg-raised"
          >
            <ClipboardCopy className="h-3.5 w-3.5" />
            {t("adm.info.paste_base")}
          </button>
        </div>
      )}

      {content && active === "faq" && (
        <div className="space-y-3">
          {content.faq.map((item, i) => (
            <Card key={i} variant="bordered">
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-fg-subtle">
                    {t("adm.info.question_n", { n: i + 1 })}
                  </span>
                  <button
                    onClick={() => removeFaq(i)}
                    className="flex items-center gap-1 rounded-lg border border-danger/20 bg-danger/8 px-2 py-1 text-xs text-danger hover:bg-danger/15"
                  >
                    <Trash2 className="h-3 w-3" />
                    {t("adm.info.delete")}
                  </button>
                </div>
                <input
                  className={textInput}
                  placeholder={isBase ? t("adm.info.q_placeholder") : base?.faq[i]?.q || t("adm.info.q_placeholder")}
                  value={item.q}
                  onChange={(e) => setFaq(i, "q", e.target.value)}
                />
                <textarea
                  className={`${textInput} min-h-[80px] resize-y`}
                  placeholder={isBase ? t("adm.info.a_placeholder") : base?.faq[i]?.a || t("adm.info.a_placeholder")}
                  value={item.a}
                  onChange={(e) => setFaq(i, "a", e.target.value)}
                />
              </div>
            </Card>
          ))}
          <Button variant="secondary" onClick={addFaq}>
            <Plus className="mr-1.5 h-4 w-4" />
            {t("adm.info.add_question")}
          </Button>
        </div>
      )}

      {content && active !== "faq" && (
        <Card variant="bordered">
          <CardHeader title={t(SECTIONS.find((s) => s.id === active)?.labelKey ?? "")} />
          <textarea
            className={`${textInput} min-h-[420px] resize-y font-mono leading-relaxed`}
            placeholder={isBase ? "" : base?.[active as TextSection]}
            value={content[active as TextSection]}
            onChange={(e) => setText(active as TextSection, e.target.value)}
          />
        </Card>
      )}
    </div>
  );
}
