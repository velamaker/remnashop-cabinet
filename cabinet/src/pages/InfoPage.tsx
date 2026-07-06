import { useEffect, useState } from "react";
import { HelpCircle, FileText, Shield, ScrollText, Star, Activity } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { infoApi, type InfoContent, type FaqItem } from "@/api/info";
import { useT } from "@/i18n/I18nContext";

const tabs = [
  { id: "faq", label: "info.tabFaq", icon: HelpCircle },
  { id: "service", label: "info.tabServers", icon: Activity },
  { id: "rules", label: "info.tabRules", icon: FileText },
  { id: "privacy", label: "info.tabPrivacy", icon: Shield },
  { id: "offer", label: "info.tabOffer", icon: ScrollText },
  { id: "statuses", label: "info.tabStatuses", icon: Star },
] as const;

type TabId = (typeof tabs)[number]["id"];

// Эмодзи-флаг страны из ISO-кода (две буквы → regional indicators).
function flagEmoji(code: string): string {
  if (!code || code.length !== 2) return "🌐";
  const base = 0x1f1e6;
  return String.fromCodePoint(
    ...[...code.toUpperCase()].map((c) => base + c.charCodeAt(0) - 65),
  );
}

// ─── Мини-markdown ───────────────────────────────────────────────────────────
// Поддерживаем то, что используется в контенте: ## / ### заголовки, - списки,
// **жирный**, абзацы (разделяются пустой строкой). Без внешних зависимостей.
function renderInline(text: string, keyBase: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={`${keyBase}-${i}`} className="text-fg font-semibold">
        {p.slice(2, -2)}
      </strong>
    ) : (
      <span key={`${keyBase}-${i}`}>{p}</span>
    ),
  );
}

function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      const key = `p-${blocks.length}`;
      blocks.push(
        <p key={key} className="text-sm leading-relaxed">
          {renderInline(para.join(" "), key)}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      const key = `ul-${blocks.length}`;
      blocks.push(
        <ul key={key} className="list-disc space-y-1 pl-4 text-sm">
          {list.map((it, i) => (
            <li key={i}>{renderInline(it, `${key}-${i}`)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushPara();
      flushList();
      continue;
    }
    if (line.startsWith("## ")) {
      flushPara();
      flushList();
      blocks.push(
        <h2 key={`h-${blocks.length}`} className="mb-2 mt-1 text-base font-semibold text-fg">
          {line.slice(3)}
        </h2>,
      );
      continue;
    }
    if (line.startsWith("### ")) {
      flushPara();
      flushList();
      blocks.push(
        <h3 key={`h-${blocks.length}`} className="mb-1 text-sm font-semibold text-fg">
          {line.slice(4)}
        </h3>,
      );
      continue;
    }
    if (line.startsWith("- ")) {
      flushPara();
      list.push(line.slice(2));
      continue;
    }
    para.push(line);
  }
  flushPara();
  flushList();

  return <div className="space-y-4 text-fg-muted">{blocks}</div>;
}

// ─── Серверы (живой статус из Remnawave бота) ────────────────────────────────
function ServiceStatus() {
  const t = useT();
  const [nodes, setNodes] = useState<
    { name: string; country_code: string; online: boolean }[]
  >([]);
  const [allOk, setAllOk] = useState(true);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    subscriptionApi
      .serviceStatus()
      .then((s) => {
        setNodes(s.nodes);
        setAllOk(s.all_operational);
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) {
    return <p className="py-6 text-center text-sm text-fg-subtle">{t("info.loadingStatus")}</p>;
  }

  return (
    <div className="space-y-5">
      <div
        className={`flex items-center gap-3 rounded-2xl border px-5 py-4 ${
          allOk
            ? "border-success/20 bg-success/10 text-success"
            : "border-warning/20 bg-warning/10 text-warning"
        }`}
      >
        <span className={`h-2.5 w-2.5 rounded-full ${allOk ? "bg-success" : "bg-warning"}`} />
        <p className="text-sm font-semibold">
          {allOk ? t("info.allOk") : t("info.someDown")}
        </p>
      </div>

      {nodes.length === 0 ? (
        <p className="py-4 text-center text-sm text-fg-subtle">{t("info.noServerData")}</p>
      ) : (
        <div className="space-y-2">
          {nodes.map((n, i) => (
            <div
              key={i}
              className="flex items-center gap-3 rounded-2xl border border-border-subtle bg-bg-subtle px-5 py-3.5"
            >
              <span className="text-xl">{flagEmoji(n.country_code)}</span>
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{n.name}</span>
              <span
                className={`flex items-center gap-1.5 text-xs font-medium ${
                  n.online ? "text-success" : "text-danger"
                }`}
              >
                <span className={`h-2 w-2 rounded-full ${n.online ? "bg-success" : "bg-danger"}`} />
                {n.online ? t("info.online") : t("info.offline")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── FAQ (аккордеон из загруженных вопросов) ─────────────────────────────────
function Faq({ items }: { items: FaqItem[] }) {
  const t = useT();
  const [open, setOpen] = useState<number | null>(null);

  if (!items.length) {
    return <p className="py-6 text-center text-sm text-fg-subtle">{t("info.emptySection")}</p>;
  }

  return (
    <div className="space-y-2">
      {items.map((item, i) => (
        <div key={i} className="overflow-hidden rounded-2xl border border-border-subtle bg-bg-subtle">
          <button
            onClick={() => setOpen(open === i ? null : i)}
            className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
          >
            <span className="text-sm font-medium text-fg">{item.q}</span>
            <span
              className={`flex-shrink-0 text-fg-muted transition-transform ${
                open === i ? "rotate-45" : ""
              }`}
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
            </span>
          </button>
          {open === i && (
            <div className="border-t border-border-subtle px-5 pb-4 pt-3">
              <p className="text-sm leading-relaxed text-fg-muted">{item.a}</p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function InfoPage() {
  const t = useT();
  const [active, setActive] = useState<TabId>("faq");
  const [content, setContent] = useState<InfoContent | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    infoApi
      .get()
      .then(setContent)
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  const renderContent = () => {
    if (active === "service") return <ServiceStatus />;
    if (!loaded) {
      return <p className="py-6 text-center text-sm text-fg-subtle">{t("common.loading")}</p>;
    }
    if (!content) {
      return <p className="py-6 text-center text-sm text-fg-subtle">{t("info.errContent")}</p>;
    }
    if (active === "faq") return <Faq items={content.faq} />;
    return <Markdown text={content[active]} />;
  };

  return (
    <div className="space-y-6">
      <h1 className="flex items-center gap-2 text-2xl font-bold text-fg">
        <svg className="h-6 w-6 text-fg-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <circle cx="12" cy="12" r="10" />
          <path strokeLinecap="round" d="M12 16v-4m0-4h.01" />
        </svg>
        {t("nav.info")}
      </h1>

      {/* Tab bar */}
      <div className="scrollbar-hide flex gap-2 overflow-x-auto pb-1">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActive(id)}
            className={`flex flex-shrink-0 items-center gap-2 rounded-xl border px-4 py-2 text-sm font-medium transition-colors ${
              active === id
                ? "border-accent bg-accent text-accent-fg"
                : "border-border-subtle bg-bg-subtle text-fg-muted hover:bg-bg-raised hover:text-fg"
            }`}
          >
            <Icon className="h-4 w-4" />
            {t(label)}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="animate-fade-in">{renderContent()}</div>
    </div>
  );
}
