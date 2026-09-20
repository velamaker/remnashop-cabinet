import { useEffect, useState } from "react";
import { clsx } from "clsx";
import { Save, CheckCircle2, SquareMenu, ChevronUp, ChevronDown, Smile } from "lucide-react";
import { menuAdminApi, type MenuConfig, type BotButton } from "@/api/menu";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { ApiError } from "@/types/api";

type Key =
  | "cabinet_miniapp"
  | "cabinet_url"
  | "connect_miniapp"
  | "connect_url"
  | "remna_sub"
  | "custom_miniapp";

// В title/desc лежат КЛЮЧИ перевода: подпись берётся при рендере (t), иначе
// строки застыли бы на языке, который стоял в момент загрузки модуля.
const META: Record<Key, { title: string; desc: string }> = {
  cabinet_miniapp: {
    title: "adm.menu.cabinet_miniapp_title",
    desc: "adm.menu.cabinet_miniapp_desc",
  },
  cabinet_url: {
    title: "adm.menu.cabinet_url_title",
    desc: "adm.menu.cabinet_url_desc",
  },
  connect_miniapp: {
    title: "adm.menu.connect_miniapp_title",
    desc: "adm.menu.connect_miniapp_desc",
  },
  connect_url: {
    title: "adm.menu.connect_url_title",
    desc: "adm.menu.connect_url_desc",
  },
  remna_sub: {
    title: "adm.menu.remna_sub_title",
    desc: "adm.menu.remna_sub_desc",
  },
  custom_miniapp: {
    title: "adm.menu.custom_miniapp_title",
    desc: "adm.menu.custom_miniapp_desc",
  },
};

const ORDER_FALLBACK: Key[] = [
  "cabinet_miniapp",
  "cabinet_url",
  "connect_miniapp",
  "connect_url",
  "remna_sub",
  "custom_miniapp",
];

// Базовые кнопки навигации (состав фиксирован в боте; ключи совпадают с NAV_KEYS).
const NAV_META: { key: string; title: string }[] = [
  { key: "nav_devices", title: "adm.menu.nav_devices" },
  { key: "nav_subscription", title: "adm.menu.nav_subscription" },
  { key: "nav_invite", title: "adm.menu.nav_invite" },
  { key: "nav_support", title: "adm.menu.nav_support" },
  { key: "nav_dashboard", title: "adm.menu.nav_dashboard" },
];

const COLOR_META: Record<string, { label: string; dot: string }> = {
  "": { label: "adm.menu.color_default", dot: "bg-fg-subtle" },
  primary: { label: "adm.menu.color_primary", dot: "bg-[#2563eb]" },
  success: { label: "adm.menu.color_success", dot: "bg-[#16a34a]" },
  danger: { label: "adm.menu.color_danger", dot: "bg-[#dc2626]" },
};

// Одна фраза — один ключ, а выделение <b>…</b> живёт внутри перевода: так
// переводчик сам решает, что выделить, и фраза не собирается из кусков.
function withBold(s: string) {
  return s
    .split(/(<b>.*?<\/b>)/g)
    .map((part, i) => (part.startsWith("<b>") ? <b key={i}>{part.slice(3, -4)}</b> : part));
}

// Палитра быстрой вставки эмодзи в текст кнопки (можно печатать/вставлять любые).
// Эмодзи по категориям (вкладка = первый эмодзи). Плюс поле «вставить любой».
const EMOJI_CATS: { icon: string; list: string[] }[] = [
  { icon: "😀", list: ["😀","😃","😄","😁","😆","😅","😂","🙂","😉","😊","😍","😘","😎","🤩","🥳","🤔","😐","😴","😇","🥺","😢","😭","😡","🤯","😱","🤗","🥰","😏","🙄","😬"] },
  { icon: "👍", list: ["👍","👎","👌","✌️","🤝","🙏","👏","💪","👊","✊","☝️","👇","👉","👈","🖐️","🤟","🫶","👋","🤙","🫵","✍️","🤲"] },
  { icon: "⚡", list: ["🚀","⚡","🔥","💥","✨","🌟","⭐","💫","🎯","🏆","🥇","🎁","🎉","🎊","💎","👑","🧨","🎈","🌈","☀️","🌙","🎀"] },
  { icon: "💳", list: ["💳","💰","💵","💸","🪙","🏦","📈","📉","📊","🧾","🛒","🛍️","🏷️","💱","💲","🤑","🎫","🧧"] },
  { icon: "🔒", list: ["🔒","🔓","🛡️","🔑","🗝️","🔐","🚨","⚠️","✅","❌","❗","❓","♻️","🆕","🆓","🔔","🔕","⛔","🚫","💠"] },
  { icon: "📱", list: ["📱","💻","🖥️","⌨️","🌐","📡","🛰️","🔌","🔋","💾","📶","🖱️","🎧","📲","🖨️","💿","🕹️","📷"] },
  { icon: "🐘", list: ["🐘","🦊","🐼","🦁","🐯","🐶","🐱","🦈","🐬","🦅","🦄","🐺","🐻","🐨","🐮","🦖","🐧","🦉","🐢","🐝"] },
  { icon: "❤️", list: ["❤️","🧡","💛","💚","💙","💜","🖤","🤍","💗","💖","💘","💝","♥️","💯","🔗","📥","📤","💬","📣","📌"] },
];

function EmojiPicker({ onPick, disabled }: { onPick: (em: string) => void; disabled?: boolean }) {
  const t = useT();
  const [cat, setCat] = useState(0);
  const [custom, setCustom] = useState("");
  const insertCustom = () => {
    const v = custom.trim();
    if (v) { onPick(v); setCustom(""); }
  };
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-0.5">
        {EMOJI_CATS.map((c, i) => (
          <button key={i} type="button" onClick={() => setCat(i)}
            className={clsx("rounded-md px-1.5 py-1 text-base leading-none transition-colors", cat === i ? "bg-accent/15" : "hover:bg-bg-overlay")}>
            {c.icon}
          </button>
        ))}
      </div>
      <div className="flex max-h-28 flex-wrap gap-0.5 overflow-y-auto">
        {EMOJI_CATS[cat]!.list.map((em) => (
          <button key={em} type="button" onClick={() => onPick(em)} disabled={disabled}
            className="rounded-md px-1.5 py-1 text-base leading-none hover:bg-bg-overlay disabled:opacity-30">
            {em}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1.5 border-t border-[var(--border)] pt-2">
        <input value={custom} onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); insertCustom(); } }}
          placeholder={t("adm.menu.emoji_any_ph")}
          className="h-7 flex-1 rounded-md border border-[var(--border)] bg-bg px-2 text-sm text-fg outline-none focus:border-accent" />
        <button type="button" onClick={insertCustom} disabled={disabled || !custom.trim()}
          className="h-7 rounded-md border border-accent bg-accent/10 px-2.5 text-xs font-medium text-accent disabled:opacity-40">
          {t("adm.menu.insert")}
        </button>
      </div>
      <p className="text-[10px] leading-snug text-fg-subtle">{t("adm.menu.emoji_any_hint")}</p>
    </div>
  );
}

// Кол-во символов «по-телеграмному» (эмодзи = 1).
// Премиум-эмодзи в тексте кнопки: <tg-emoji emoji-id="123">⭐</tg-emoji>.
// Бот парсит тег и ставит анимированный эмодзи премиум-юзерам, остальным — fallback.
const TG_EMOJI_RE = /<tg-emoji emoji-id="\d+">([^<]*)<\/tg-emoji>/g;
const cleanBtnText = (s: string) => s.replace(TG_EMOJI_RE, "$1");
const hasPremiumEmoji = (s: string) => s.includes("<tg-emoji");
// Длину считаем по ЧИСТОМУ тексту (Telegram лимит 64 — на отображаемый текст,
// тег заменяется своим fallback при отправке).
const btnLen = (s: string) => Array.from(cleanBtnText(s)).length;

// Короткая метка «что уже настроено» рядом со свёрнутой строкой — чтобы не
// раскрывать редактор ради проверки, задан ли уже свой текст/цвет.
function summaryLabel(text: string, color: string) {
  const parts: string[] = [];
  if (text) parts.push(translate("adm.menu.sum_custom_text"));
  if (color) {
    const labelKey = COLOR_META[color]?.label;
    parts.push(labelKey ? translate(labelKey).toLowerCase() : color);
  }
  return parts.join(", ");
}

export default function AdminMenuPage() {
  const t = useT();
  const [cfg, setCfg] = useState<MenuConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emojiFor, setEmojiFor] = useState<string | null>(null);
  const [premiumId, setPremiumId] = useState("");
  const [premiumFb, setPremiumFb] = useState("");
  // Аккордеон: раскрыт редактор текста/цвета не больше чем у одной кнопки сразу
  // (общий и для кнопок доступа, и для навигации).
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    menuAdminApi
      .get()
      .then(setCfg)
      .catch((e) => setError(e instanceof ApiError ? e.detail : translate("adm.menu.err_load")))
      .finally(() => setLoading(false));
  }, []);

  const toggle = (key: Key) => cfg && setCfg({ ...cfg, [key]: !cfg[key] });
  const toggleExpanded = (key: string) => setExpanded((c) => (c === key ? null : key));

  // Список ключей в текущем порядке (с фолбэком, если бэкенд не прислал order).
  const orderKeys: Key[] = (() => {
    const fromCfg = (cfg?.order ?? []).filter((k): k is Key => k in META);
    for (const k of ORDER_FALLBACK) if (!fromCfg.includes(k)) fromCfg.push(k);
    return fromCfg;
  })();

  const move = (idx: number, dir: -1 | 1) => {
    if (!cfg) return;
    const j = idx + dir;
    const a = orderKeys[idx];
    const b = orderKeys[j];
    if (a === undefined || b === undefined) return;
    const next = [...orderKeys];
    next[idx] = b;
    next[j] = a;
    setCfg({ ...cfg, order: next });
  };

  // Кастомный текст/эмодзи/цвет для кнопок меню (веб-кабинет + навигация).
  const setText = (key: string, val: string) => {
    // Не режем сырой текст (в нём может быть <tg-emoji> тег) — ограничиваем по
    // ЧИСТОЙ длине (btnLen игнорирует теги).
    if (!cfg || btnLen(val) > 64) return;
    setCfg({ ...cfg, texts: { ...(cfg.texts ?? {}), [key]: val } });
  };
  const addPremiumEmoji = (key: string, emojiId: string, fallback: string) => {
    if (!cfg || !/^\d+$/.test(emojiId.trim())) return;
    const fb = (fallback.trim() || "⭐").slice(0, 2);
    const cur = cfg.texts?.[key] ?? cfg.defaults?.[key] ?? "";
    const tag = `<tg-emoji emoji-id="${emojiId.trim()}">${fb}</tg-emoji>`;
    const next = cur ? `${tag} ${cur}` : tag;
    if (btnLen(next) > 64) return;
    setCfg({ ...cfg, texts: { ...(cfg.texts ?? {}), [key]: next } });
  };
  const clearPremiumEmoji = (key: string) => {
    if (!cfg) return;
    const cur = cfg.texts?.[key] ?? "";
    setCfg({ ...cfg, texts: { ...(cfg.texts ?? {}), [key]: cur.replace(TG_EMOJI_RE, "").replace(/\s{2,}/g, " ").trim() } });
  };
  const addEmoji = (key: string, em: string) => {
    if (!cfg) return;
    // Если своей подписи ещё нет — начинаем от реального дефолтного текста,
    // иначе эмодзи просто заменит собой пустое поле и подпись пропадёт.
    const cur = cfg.texts?.[key] ?? cfg.defaults?.[key] ?? "";
    if (btnLen(cur) >= 64) return;
    setCfg({ ...cfg, texts: { ...(cfg.texts ?? {}), [key]: cur + " " + em } });
  };
  const setColor = (key: string, c: string) =>
    cfg && setCfg({ ...cfg, colors: { ...(cfg.colors ?? {}), [key]: c } });

  // Редактор подписи (эмодзи) + цвета для одной кнопки — используется и для
  // кнопок доступа, и для навигации; показывается только когда строка раскрыта.
  // Что бэкенд умеет в этом разделе. Поля нет — умеет всё (наш бэкенд его не шлёт).
  const sup = cfg?.supported ?? {};

  const renderStyleEditor = (key: string) => {
    const text = cfg?.texts?.[key] ?? "";
    const color = cfg?.colors?.[key] ?? "";
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="relative flex min-w-0 flex-1 items-center">
            <input
              value={text}
              onChange={(e) => setText(key, e.target.value)}
              placeholder={
                cfg?.defaults?.[key]
                  ? t("adm.menu.text_default_ph", { text: cfg.defaults[key] })
                  : t("adm.menu.text_ph")
              }
              className="w-full rounded-lg border border-[var(--border)] bg-bg px-2.5 py-1.5 text-sm text-fg outline-none focus:border-accent"
            />
            <span className="pointer-events-none absolute right-2 text-[10px] tabular-nums text-fg-subtle">
              {btnLen(text)}/64
            </span>
          </div>
          <button
            type="button"
            aria-label={t("adm.menu.add_emoji")}
            onClick={() => setEmojiFor((c) => (c === key ? null : key))}
            className={clsx(
              "shrink-0 rounded-lg border p-1.5 transition-colors",
              emojiFor === key
                ? "border-accent bg-accent/10 text-accent"
                : "border-[var(--border)] text-fg-muted hover:text-fg",
            )}
          >
            <Smile className="h-4 w-4" />
          </button>
        </div>

        {emojiFor === key && (
          <div className="space-y-2 rounded-lg border border-[var(--border)] bg-bg p-2">
            <EmojiPicker onPick={(em) => addEmoji(key, em)} disabled={btnLen(text) >= 64} />
            <div className="border-t border-[var(--border)] pt-2">
              <p className="mb-1.5 text-[11px] font-semibold text-fg-muted">{t("adm.menu.premium_title")}</p>
              {hasPremiumEmoji(text) ? (
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-success">{t("adm.menu.premium_added")}</span>
                  <button type="button" onClick={() => clearPremiumEmoji(key)} className="font-medium text-danger hover:underline">{t("adm.menu.premium_remove")}</button>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-1.5">
                  <input value={premiumId} onChange={(e) => setPremiumId(e.target.value.replace(/\D/g, ""))}
                    placeholder="emoji-id" inputMode="numeric"
                    className="h-7 w-32 rounded-md border border-[var(--border)] bg-bg px-2 text-xs text-fg outline-none focus:border-accent" />
                  <input value={premiumFb} onChange={(e) => setPremiumFb(e.target.value)}
                    placeholder="fallback ⭐" maxLength={2}
                    className="h-7 w-20 rounded-md border border-[var(--border)] bg-bg px-2 text-xs text-fg outline-none focus:border-accent" />
                  <button type="button"
                    onClick={() => { addPremiumEmoji(key, premiumId, premiumFb); setPremiumId(""); setPremiumFb(""); }}
                    disabled={!/^\d+$/.test(premiumId)}
                    className="h-7 rounded-md border border-accent bg-accent/10 px-2.5 text-xs font-medium text-accent disabled:opacity-40">
                    {t("adm.menu.insert")}
                  </button>
                </div>
              )}
              <p className="mt-1 text-[10px] leading-snug text-fg-subtle">
                {t("adm.menu.premium_hint")}
              </p>
            </div>
          </div>
        )}

        {/* У кнопок чужого бота цвета нет вовсе — выбор прячем, чтобы не предлагать
            настройку, которая никуда не применится. */}
        {sup.colors !== false && (
        <div className="flex flex-wrap gap-1.5">
          {["", "primary", "success", "danger"].map((c) => {
            const m = COLOR_META[c] ?? COLOR_META[""]!;
            return (
              <button key={c} type="button"
                onClick={() => setColor(key, c)}
                className={clsx(
                  "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors",
                  color === c
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-[var(--border)] text-fg-muted hover:text-fg",
                )}>
                <span className={`h-2.5 w-2.5 rounded-full ${m.dot}`} />
                {t(m.label)}
              </button>
            );
          })}
        </div>
        )}
      </div>
    );
  };

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      // На чужом боте применимы только подписи и цвета его навигации. Слать
      // туда весь конфиг нельзя: бэкенд честно откажет на первой же нашей кнопке,
      // и человек увидит красную ошибку вместо сохранения.
      const payload =
        (cfg.supported?.access_buttons === false)
          ? { texts: cfg.texts, colors: cfg.colors }
          : cfg;
      const next = await menuAdminApi.update(payload);
      setCfg(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : translate("adm.menu.err_save"));
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
          {t("adm.menu.title")}
        </h1>
        <button
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? t("adm.menu.saved") : saving ? t("adm.menu.saving") : t("adm.menu.save")}
        </button>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {/* На чужом боте наши кнопки доступа поставить некуда — вместо формы,
          которая ответит отказом на сохранении, объясняем это одной строкой. */}
      {sup.access_buttons === false && (
        <p className="rounded-xl border border-border-subtle bg-bg-subtle px-4 py-3 text-xs leading-relaxed text-fg-muted">
          {t("adm.menu.foreign_notice")}
        </p>
      )}

      {/* Основные кнопки: доступ (кабинет/подключиться, с галочкой и порядком) + навигация (состав фиксирован) */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="text-sm font-semibold text-fg">
          {sup.access_buttons === false ? t("adm.menu.sec_foreign_title") : t("adm.menu.sec_main_title")}
        </h2>
        <p className="mb-3 mt-0.5 text-xs text-fg-muted">
          {sup.access_buttons === false ? t("adm.menu.sec_foreign_hint") : t("adm.menu.sec_main_hint")}
        </p>

        {/* Кнопки доступа в кабинет — наши. На чужом боте их некуда поставить,
            поэтому список не показываем вовсе, а не рисуем неактивные галочки. */}
        {sup.access_buttons !== false && (
        <div className="space-y-1.5">
          {orderKeys.map((key, idx) => {
            const on = cfg[key];
            const meta = META[key];
            const isOpen = expanded === key;
            const summary = summaryLabel(cfg.texts?.[key] ?? "", cfg.colors?.[key] ?? "");
            return (
              <div
                key={key}
                className={clsx(
                  "overflow-hidden rounded-xl border transition-colors",
                  on ? "border-[var(--border)] bg-bg" : "border-[var(--border)] bg-bg opacity-60",
                )}
              >
                <div className="flex items-center gap-3 px-3 py-2.5">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => toggle(key)}
                    className="h-4 w-4 shrink-0 cursor-pointer accent-[var(--accent)]"
                  />
                  <div className="min-w-0 flex-1">
                    <span className="text-sm font-semibold text-fg">{t(meta.title)}</span>
                    <p className="mt-0.5 truncate text-xs text-fg-muted">{t(meta.desc)}</p>
                  </div>
                  <div className="flex shrink-0 flex-col gap-1">
                    <button
                      type="button"
                      onClick={() => move(idx, -1)}
                      disabled={idx === 0}
                      aria-label={t("adm.menu.move_up")}
                      className="rounded-lg border border-border-subtle p-1 text-fg-muted transition-colors hover:bg-bg-overlay hover:text-fg disabled:cursor-not-allowed disabled:opacity-30"
                    >
                      <ChevronUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => move(idx, 1)}
                      disabled={idx === orderKeys.length - 1}
                      aria-label={t("adm.menu.move_down")}
                      className="rounded-lg border border-border-subtle p-1 text-fg-muted transition-colors hover:bg-bg-overlay hover:text-fg disabled:cursor-not-allowed disabled:opacity-30"
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  {on && (
                    <button
                      type="button"
                      onClick={() => toggleExpanded(key)}
                      aria-label={t("adm.menu.edit_style")}
                      className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border-subtle px-2 py-1.5 text-fg-muted transition-colors hover:bg-bg-overlay hover:text-fg"
                    >
                      {summary && <span className="text-[11px] text-fg-subtle">{summary}</span>}
                      <ChevronDown className={clsx("h-4 w-4 transition-transform", isOpen && "rotate-180")} />
                    </button>
                  )}
                </div>

                {on && isOpen && (
                  <div className="border-t border-border-subtle px-3 pb-3 pt-3">
                    {key === "custom_miniapp" && (
                      <div className="mb-3">
                        <label className="mb-1 block text-[11px] font-semibold text-fg-muted">
                          {t("adm.menu.custom_url_label")}
                        </label>
                        <input
                          value={cfg.custom_url ?? ""}
                          onChange={(e) => setCfg({ ...cfg, custom_url: e.target.value })}
                          placeholder="https://sub.example.com/app"
                          className="w-full rounded-lg border border-[var(--border)] bg-bg px-2.5 py-1.5 text-sm text-fg outline-none focus:border-accent"
                        />
                        <p className="mt-1 text-[10px] leading-snug text-fg-subtle">
                          {withBold(t("adm.menu.custom_url_hint"))}
                        </p>
                      </div>
                    )}
                    {renderStyleEditor(key)}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        )}

        {/* «Подарить подписку» — тоже наша механика: у чужого бота её нет. */}
        {sup.gift !== false && (<>
        <p className="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
          {t("adm.menu.gifts")}
        </p>
        <div className="space-y-1.5">
          {(() => {
            const on = cfg.gift !== false;
            const isOpen = expanded === "gift";
            const summary = summaryLabel(cfg.texts?.gift ?? "", cfg.colors?.gift ?? "");
            return (
              <div className={clsx("overflow-hidden rounded-xl border border-[var(--border)] bg-bg", !on && "opacity-60")}>
                <div className="flex items-center gap-3 px-3 py-2.5">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => setCfg({ ...cfg, gift: !on })}
                    className="h-4 w-4 shrink-0 cursor-pointer accent-[var(--accent)]"
                  />
                  <div className="min-w-0 flex-1">
                    <span className="text-sm font-semibold text-fg">{t("adm.menu.gift_title")}</span>
                    <p className="mt-0.5 truncate text-xs text-fg-muted">
                      {t("adm.menu.gift_desc")}
                    </p>
                  </div>
                  {on && (
                    <button
                      type="button"
                      onClick={() => toggleExpanded("gift")}
                      aria-label={t("adm.menu.edit_style")}
                      className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border-subtle px-2 py-1.5 text-fg-muted transition-colors hover:bg-bg-overlay hover:text-fg"
                    >
                      {summary && <span className="text-[11px] text-fg-subtle">{summary}</span>}
                      <ChevronDown className={clsx("h-4 w-4 transition-transform", isOpen && "rotate-180")} />
                    </button>
                  )}
                </div>
                {on && isOpen && (
                  <div className="border-t border-border-subtle px-3 pb-3 pt-3">{renderStyleEditor("gift")}</div>
                )}
              </div>
            );
          })()}
        </div>
        </>)}

        <p className="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
          {t("adm.menu.nav_heading")}
        </p>
        <div className="space-y-1.5">
          {/* Кнопки берём у бэкенда, если он их прислал: у чужого бота меню своё
              (Личный кабинет, Баланс, Партнёрка…), и показывать вместо него наши
              названия — заставлять человека править несуществующие кнопки. */}
          {(cfg.nav_items?.map((it) => ({ key: it.key, title: it.label, raw: true })) ??
            NAV_META.map((n) => ({ ...n, raw: false }))).map((n) => {
            const isOpen = expanded === n.key;
            const summary = summaryLabel(cfg.texts?.[n.key] ?? "", cfg.colors?.[n.key] ?? "");
            return (
              <div key={n.key} className="overflow-hidden rounded-xl border border-[var(--border)] bg-bg">
                <button
                  type="button"
                  onClick={() => toggleExpanded(n.key)}
                  className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">
                    {n.raw ? n.title : t(n.title)}
                  </span>
                  {summary && <span className="shrink-0 text-[11px] text-fg-subtle">{summary}</span>}
                  <ChevronDown className={clsx("h-4 w-4 shrink-0 text-fg-muted transition-transform", isOpen && "rotate-180")} />
                </button>
                {isOpen && (
                  <div className="border-t border-border-subtle px-3 pb-3 pt-3">
                    {renderStyleEditor(n.key)}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <p className="mt-4 text-xs text-fg-subtle">
          {t("adm.menu.footer_note")}
        </p>
      </section>

      <BotButtonColors />
    </div>
  );
}

// ── Основные кнопки бота (авторская задумка: settings.menu.buttons[]) ──────

function BotButtonColors() {
  const t = useT();
  const [buttons, setButtons] = useState<BotButton[]>([]);
  const [colors, setColors] = useState<Record<number, string>>({}); // index → "" | primary…
  const [texts, setTexts] = useState<Record<number, string>>({}); // index → текст кнопки
  const [expanded, setExpanded] = useState<number | null>(null); // какая строка раскрыта
  const [emojiOpen, setEmojiOpen] = useState(false); // палитра внутри раскрытой строки
  const [premiumId, setPremiumId] = useState("");
  const [premiumFb, setPremiumFb] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    menuAdminApi.getButtons()
      .then((r) => {
        setButtons(r.buttons);
        setColors(Object.fromEntries(r.buttons.map((b) => [b.index, b.color ?? ""])));
        setTexts(Object.fromEntries(r.buttons.map((b) => [b.index, b.text ?? ""])));
      })
      .catch((e) => setErr(e instanceof ApiError ? e.detail : translate("adm.menu.err_generic")))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  // Добавить эмодзи в конец текста кнопки (не превышая 32 символа).
  const addEmoji = (idx: number, emoji: string) =>
    setTexts((p) => {
      const cur = p[idx] ?? "";
      if (btnLen(cur) >= 32) return p;
      return { ...p, [idx]: cur + emoji };
    });

  const setBtnText = (idx: number, val: string) =>
    setTexts((p) => (btnLen(val) > 32 ? p : { ...p, [idx]: val }));
  const addPremium = (idx: number) => {
    if (!/^\d+$/.test(premiumId.trim())) return;
    const fb = (premiumFb.trim() || "⭐").slice(0, 2);
    setTexts((p) => {
      const cur = p[idx] ?? "";
      const tag = `<tg-emoji emoji-id="${premiumId.trim()}">${fb}</tg-emoji>`;
      const next = cur ? `${tag} ${cur}` : tag;
      if (btnLen(next) > 32) return p;
      return { ...p, [idx]: next };
    });
    setPremiumId(""); setPremiumFb("");
  };
  const clearPremium = (idx: number) =>
    setTexts((p) => ({ ...p, [idx]: (p[idx] ?? "").replace(TG_EMOJI_RE, "").replace(/\s{2,}/g, " ").trim() }));

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const colorsPayload: Record<number, string | null> = {};
      for (const [idx, c] of Object.entries(colors)) colorsPayload[Number(idx)] = c || null;
      const textsPayload: Record<number, string> = {};
      for (const [idx, t] of Object.entries(texts)) {
        const v = (t ?? "").trim();
        if (v) textsPayload[Number(idx)] = v; // пустые не шлём — бэкенд не даст пустой текст
      }
      const r = await menuAdminApi.saveButtons({ colors: colorsPayload, texts: textsPayload });
      setButtons(r.buttons);
      setTexts(Object.fromEntries(r.buttons.map((b) => [b.index, b.text ?? ""])));
      setSaved(true); setTimeout(() => setSaved(false), 2000);
    } catch (e) { setErr(e instanceof ApiError ? e.detail : translate("adm.menu.err_generic")); }
    finally { setSaving(false); }
  };

  if (loading) return null;
  // Показываем только заполненные/активные кнопки бота (пустые слоты не нужны).
  // Пустой список бывает и потому, что у чужого бота такой механики нет вовсе —
  // тогда блока тоже быть не должно.
  const shown = buttons.filter((b) => b.is_active || (b.text && b.text !== "btn-test"));
  if (shown.length === 0) return null;

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-1 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-fg">{t("adm.menu.extra_title")}</h2>
        <button onClick={save} disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-60">
          {saved ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
          {saved ? t("adm.menu.saved") : saving ? "…" : t("adm.menu.save")}
        </button>
      </div>
      <p className="mb-3 text-xs text-fg-muted">
        {t("adm.menu.extra_hint")}
      </p>
      {err && <p className="mb-2 text-xs text-danger">{err}</p>}
      <div className="space-y-1.5">
        {shown.map((b) => {
          const text = texts[b.index] ?? "";
          const color = colors[b.index] ?? "";
          const len = btnLen(text);
          const isOpen = expanded === b.index;
          const m = COLOR_META[color] ?? COLOR_META[""]!;
          return (
            <div key={b.index} className="overflow-hidden rounded-xl border border-[var(--border)] bg-bg">
              <button
                type="button"
                onClick={() => { setExpanded((c) => (c === b.index ? null : b.index)); setEmojiOpen(false); }}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
              >
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${m.dot}`} />
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">
                  {text || t("adm.menu.button_n", { n: b.index })}
                </span>
                <ChevronDown className={clsx("h-4 w-4 shrink-0 text-fg-muted transition-transform", isOpen && "rotate-180")} />
              </button>

              {isOpen && (
                <div className="border-t border-border-subtle px-3 pb-3 pt-3">
                  {/* Строка редактирования текста + вставка эмодзи */}
                  <div className="flex items-center gap-2">
                    <div className="relative flex min-w-0 flex-1 items-center">
                      <input
                        value={text}
                        onChange={(e) => setBtnText(b.index, e.target.value)}
                        placeholder={t("adm.menu.button_n", { n: b.index })}
                        className="w-full rounded-lg border border-[var(--border)] bg-bg-subtle px-2.5 py-1.5 text-sm text-fg outline-none focus:border-accent"
                      />
                      <span className="pointer-events-none absolute right-2 text-[10px] tabular-nums text-fg-subtle">
                        {len}/32
                      </span>
                    </div>
                    <button
                      type="button"
                      aria-label={t("adm.menu.add_emoji")}
                      onClick={() => setEmojiOpen((v) => !v)}
                      className={clsx(
                        "shrink-0 rounded-lg border p-1.5 transition-colors",
                        emojiOpen
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-[var(--border)] text-fg-muted hover:text-fg",
                      )}>
                      <Smile className="h-4 w-4" />
                    </button>
                  </div>

                  {/* Палитра эмодзи + премиум-эмодзи (раскрывается под кнопкой) */}
                  {emojiOpen && (
                    <div className="mt-2 space-y-2 rounded-lg border border-[var(--border)] bg-bg-subtle p-2">
                      <EmojiPicker onPick={(em) => addEmoji(b.index, em)} disabled={len >= 32} />
                      <div className="border-t border-[var(--border)] pt-2">
                        <p className="mb-1.5 text-[11px] font-semibold text-fg-muted">{t("adm.menu.premium_title")}</p>
                        {hasPremiumEmoji(text) ? (
                          <div className="flex items-center justify-between gap-2 text-xs">
                            <span className="text-success">{t("adm.menu.premium_added")}</span>
                            <button type="button" onClick={() => clearPremium(b.index)} className="font-medium text-danger hover:underline">{t("adm.menu.premium_remove")}</button>
                          </div>
                        ) : (
                          <div className="flex flex-wrap items-center gap-1.5">
                            <input value={premiumId} onChange={(e) => setPremiumId(e.target.value.replace(/\D/g, ""))}
                              placeholder="emoji-id" inputMode="numeric"
                              className="h-7 w-32 rounded-md border border-[var(--border)] bg-bg px-2 text-xs text-fg outline-none focus:border-accent" />
                            <input value={premiumFb} onChange={(e) => setPremiumFb(e.target.value)}
                              placeholder="fallback ⭐" maxLength={2}
                              className="h-7 w-20 rounded-md border border-[var(--border)] bg-bg px-2 text-xs text-fg outline-none focus:border-accent" />
                            <button type="button" onClick={() => addPremium(b.index)} disabled={!/^\d+$/.test(premiumId)}
                              className="h-7 rounded-md border border-accent bg-accent/10 px-2.5 text-xs font-medium text-accent disabled:opacity-40">
                              {t("adm.menu.insert")}
                            </button>
                          </div>
                        )}
                        <p className="mt-1 text-[10px] leading-snug text-fg-subtle">
                          {t("adm.menu.premium_hint_short")}
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Цвета */}
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {["", "primary", "success", "danger"].map((c) => {
                      const cm = COLOR_META[c] ?? COLOR_META[""]!;
                      return (
                        <button key={c} type="button"
                          onClick={() => setColors((p) => ({ ...p, [b.index]: c }))}
                          className={clsx(
                            "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors",
                            (colors[b.index] ?? "") === c
                              ? "border-accent bg-accent/10 text-accent"
                              : "border-[var(--border)] text-fg-muted hover:text-fg",
                          )}>
                          <span className={`h-2.5 w-2.5 rounded-full ${cm.dot}`} />
                          {t(cm.label)}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
