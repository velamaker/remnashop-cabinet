import { useState } from "react";
import { Link } from "react-router-dom";
import { Stethoscope, CheckCircle2, AlertTriangle, XCircle, Loader2, RefreshCw, Copy, LifeBuoy, ChevronRight } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { supportApi } from "@/api/support";
import { useT } from "@/i18n/I18nContext";
import { formatDate } from "@/lib/format";

// Автодиагностика «VPN не работает» — фронтовый визард поверх существующих
// эндпоинтов (подписка / устройства / статус нод). Снижает нагрузку на поддержку:
// сначала самопроверка, и только если не помогло — тикет с уже собранным контекстом.

type CheckStatus = "ok" | "warn" | "fail";
interface Check {
  key: string;
  status: CheckStatus;
  label: string;
  hint?: string;
  cta?: { label: string; to: string };
}

const ICON: Record<CheckStatus, typeof CheckCircle2> = { ok: CheckCircle2, warn: AlertTriangle, fail: XCircle };
const TONE: Record<CheckStatus, string> = { ok: "text-success", warn: "text-amber-500", fail: "text-danger" };

export function DiagnosticWizard() {
  const t = useT();
  const [state, setState] = useState<"idle" | "run" | "done">("idle");
  const [checks, setChecks] = useState<Check[]>([]);
  const [subUrl, setSubUrl] = useState<string | null>(null);
  const [reissuing, setReissuing] = useState(false);
  const [reissued, setReissued] = useState(false);
  const [copied, setCopied] = useState(false);
  const [ticket, setTicket] = useState<"idle" | "busy" | "done" | "err">("idle");
  const [ticketId, setTicketId] = useState<number | null>(null);

  const run = async () => {
    setState("run");
    setChecks([]);
    setTicket("idle");
    setReissued(false);
    const out: Check[] = [];
    const now = Date.now();

    let sub: Awaited<ReturnType<typeof subscriptionApi.current>> = null;
    try {
      sub = await subscriptionApi.current();
    } catch {
      /* ниже трактуем как отсутствие подписки */
    }

    if (!sub) {
      out.push({ key: "sub", status: "fail", label: t("diag.sub.none.label"), hint: t("diag.sub.none.hint"), cta: { label: t("diag.cta.plans"), to: "/billing" } });
    } else {
      const expired = !!sub.expire_at && new Date(sub.expire_at).getTime() < now;
      if (sub.status !== "ACTIVE" || expired) {
        out.push({ key: "sub", status: "fail", label: expired ? t("diag.sub.expired") : t("diag.sub.inactive"), hint: t("diag.sub.bad.hint"), cta: { label: t("diag.cta.renew"), to: "/billing" } });
      } else {
        out.push({ key: "sub", status: "ok", label: t("diag.sub.active", { date: formatDate(sub.expire_at) }) });
      }
      if (sub.traffic_limit > 0) {
        const used = sub.used_traffic_bytes ?? 0;
        if (used >= sub.traffic_limit) out.push({ key: "traffic", status: "fail", label: t("diag.traffic.out"), hint: t("diag.traffic.out.hint"), cta: { label: t("diag.cta.changePlan"), to: "/billing" } });
        else out.push({ key: "traffic", status: "ok", label: t("diag.traffic.ok") });
      } else {
        out.push({ key: "traffic", status: "ok", label: t("diag.traffic.unlimited") });
      }
      setSubUrl(sub.url ?? null);

      try {
        const d = await subscriptionApi.devices();
        if (d.current_count > d.max_count) out.push({ key: "dev", status: "fail", label: t("diag.dev.over", { cur: d.current_count, max: d.max_count }), hint: t("diag.dev.over.hint"), cta: { label: t("diag.cta.devices"), to: "/subscription" } });
        else if (d.current_count >= d.max_count) out.push({ key: "dev", status: "warn", label: t("diag.dev.reached", { cur: d.current_count, max: d.max_count }), hint: t("diag.dev.reached.hint"), cta: { label: t("diag.cta.devices"), to: "/subscription" } });
        else out.push({ key: "dev", status: "ok", label: t("diag.dev.ok", { cur: d.current_count, max: d.max_count }) });
      } catch {
        /* устройства недоступны — пропускаем чек */
      }
    }

    try {
      const svc = await subscriptionApi.serviceStatus();
      const online = svc.nodes.filter((n) => n.online).length;
      if (svc.nodes.length > 0 && online === 0) out.push({ key: "srv", status: "fail", label: t("diag.srv.down"), hint: t("diag.srv.down.hint") });
      else if (!svc.all_operational) out.push({ key: "srv", status: "warn", label: t("diag.srv.partial", { online, total: svc.nodes.length }), hint: t("diag.srv.partial.hint") });
      else out.push({ key: "srv", status: "ok", label: t("diag.srv.ok") });
    } catch {
      /* статус недоступен — пропускаем чек */
    }

    setChecks(out);
    setState("done");
  };

  const reissue = async () => {
    setReissuing(true);
    try {
      await subscriptionApi.reissue();
      const s = await subscriptionApi.current();
      setSubUrl(s?.url ?? null);
      setReissued(true);
    } catch {
      /* игнор — кнопка останется доступной */
    } finally {
      setReissuing(false);
    }
  };

  const copyLink = async () => {
    if (!subUrl) return;
    try {
      await navigator.clipboard.writeText(subUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard недоступен */
    }
  };

  const createTicket = async () => {
    setTicket("busy");
    const summary = checks.map((c) => `${c.status === "ok" ? "✅" : c.status === "warn" ? "⚠️" : "❌"} ${c.label}`).join("\n");
    try {
      const { id } = await supportApi.create(t("diag.ticket.subject"), t("diag.ticket.body", { summary }));
      setTicketId(id);
      setTicket("done");
    } catch {
      setTicket("err");
    }
  };

  const hasProblem = checks.some((c) => c.status !== "ok");

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent">
          <Stethoscope className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-bold text-fg">{t("diag.title")}</h3>
          <p className="mt-0.5 text-xs text-fg-muted">{t("diag.subtitle")}</p>
        </div>
        {state !== "run" && (
          <button
            type="button"
            onClick={run}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-accent px-3 py-2 text-sm font-semibold text-accent-fg hover:bg-accent/90"
          >
            {state === "done" ? <RefreshCw className="h-4 w-4" /> : <Stethoscope className="h-4 w-4" />}
            {state === "done" ? t("diag.again") : t("diag.check")}
          </button>
        )}
        {state === "run" && <Loader2 className="mt-2 h-5 w-5 shrink-0 animate-spin text-accent" />}
      </div>

      {state === "done" && (
        <div className="mt-4 space-y-2">
          {checks.map((c) => {
            const Icon = ICON[c.status];
            return (
              <div key={c.key} className="rounded-xl border border-border-subtle bg-bg px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <Icon className={`h-4 w-4 shrink-0 ${TONE[c.status]}`} />
                  <span className="text-sm font-medium text-fg">{c.label}</span>
                  {c.cta && (
                    <Link to={c.cta.to} className="ml-auto inline-flex items-center gap-0.5 text-xs font-semibold text-accent hover:underline">
                      {c.cta.label}
                      <ChevronRight className="h-3.5 w-3.5" />
                    </Link>
                  )}
                </div>
                {c.hint && <p className="mt-1 pl-6 text-xs text-fg-muted">{c.hint}</p>}
              </div>
            );
          })}

          {/* Переустановка конфига — актуально даже если всё зелёное */}
          <div className="rounded-xl border border-border-subtle bg-bg px-3 py-3">
            <p className="text-sm font-medium text-fg">
              {hasProblem ? t("diag.reinstall.problem") : t("diag.reinstall.ok")}
            </p>
            <p className="mt-1 text-xs text-fg-muted">{t("diag.reinstall.hint")}</p>
            <div className="mt-2.5 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={reissue}
                disabled={reissuing}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg-subtle/70 disabled:opacity-50"
              >
                {reissuing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                {reissued ? t("diag.reissued") : t("diag.reissue")}
              </button>
              {subUrl && (
                <button
                  type="button"
                  onClick={copyLink}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg-subtle/70"
                >
                  <Copy className="h-4 w-4" />
                  {copied ? t("diag.copied") : t("diag.copy")}
                </button>
              )}
            </div>
          </div>

          {/* Создать тикет с уже собранной диагностикой */}
          {ticket === "done" ? (
            <p className="rounded-xl border border-success/40 bg-success/10 px-3 py-2.5 text-sm text-success">
              {t("diag.ticket.done", { id: ticketId ?? 0 })}
            </p>
          ) : (
            <button
              type="button"
              onClick={createTicket}
              disabled={ticket === "busy"}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm font-semibold text-fg hover:bg-bg/70 disabled:opacity-50"
            >
              {ticket === "busy" ? <Loader2 className="h-4 w-4 animate-spin" /> : <LifeBuoy className="h-4 w-4" />}
              {t("diag.ticket.create")}
            </button>
          )}
          {ticket === "err" && <p className="text-xs text-danger">{t("diag.ticket.err")}</p>}
        </div>
      )}
    </section>
  );
}
