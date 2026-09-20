import { useEffect, useState } from "react";
import { AlertCircle, ShieldAlert } from "lucide-react";
import { auditAdminApi, type AuditEntry } from "@/api/admin";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { ApiError } from "@/types/api";

const METHOD_COLOR: Record<string, string> = {
  POST: "text-success",
  PUT: "text-accent",
  PATCH: "text-accent",
  DELETE: "text-danger",
};

// Короткое читаемое действие из пути (например, /gateways/5/fields/api_key).
function actionLabel(path: string): string {
  const p = path.replace(/^\/api\/v1\/admin\//, "");
  const field = p.match(/^gateways\/\d+\/fields\/(.+)$/);
  if (field) return translate("adm.audit.act_gateway_field", { key: field[1] ?? "" });
  const map: [RegExp, string][] = [
    [/^gateways\/\d+\/toggle$/, "adm.audit.act_gateway_toggle"],
    [/^plans/, "adm.audit.act_plans"],
    [/^promocodes/, "adm.audit.act_promocodes"],
    [/^users\/\d+\/block$/, "adm.audit.act_user_block"],
    [/^users\/\d+\/role$/, "adm.audit.act_user_role"],
    [/^users\/\d+\/discount$/, "adm.audit.act_user_discount"],
    [/^users\/bulk\/days$/, "adm.audit.act_bulk_days"],
    [/^users\/bulk\/message$/, "adm.audit.act_bulk_message"],
    [/^users\/bulk\/message\/test$/, "adm.audit.act_bulk_message_test"],
    [/^users\/bulk\/jobs\/\d+\/cancel$/, "adm.audit.act_bulk_job_cancel"],
    [/^users\/bulk\/jobs\/\d+\/resume$/, "adm.audit.act_bulk_job_resume"],
    [/^users\/bulk-action$/, "adm.audit.act_bulk_action"],
    [/^broadcasts/, "adm.audit.act_broadcasts"],
    [/^ad-links/, "adm.audit.act_ad_links"],
    [/^appearance/, "adm.audit.act_appearance"],
    [/^settings/, "adm.audit.act_settings"],
    [/^support/, "adm.audit.act_support"],
  ];
  for (const [re, key] of map) {
    // Замена функцией: перевод подставляется как есть, без магии $1/$& внутри строки.
    if (re.test(p)) return p.replace(re, () => translate(key));
  }
  return p;
}

function fmt(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function AdminAuditPage() {
  const t = useT();
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actor, setActor] = useState("");
  const [method, setMethod] = useState("");
  const [path, setPath] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const load = () => {
    setLoading(true);
    setError(null);
    auditAdminApi
      .list({ limit: 200, actor: actor || undefined, method: method || undefined, path: path || undefined, date_from: dateFrom || undefined, date_to: dateTo || undefined })
      .then((r) => setItems(r.items))
      .catch((e) => setError(e instanceof ApiError ? e.detail : t("adm.audit.err_generic")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reset = () => {
    setActor(""); setMethod(""); setPath(""); setDateFrom(""); setDateTo("");
    setTimeout(load, 0);
  };

  const inputCls = "rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <ShieldAlert className="h-5 w-5 text-fg-muted" />
        <h1 className="text-2xl font-bold text-fg">{t("adm.audit.title")}</h1>
      </div>

      <p className="text-sm text-fg-muted">
        {t("adm.audit.intro")}
      </p>

      {/* Фильтры */}
      <div className="flex flex-wrap items-end gap-2 rounded-2xl border border-border-subtle bg-bg-subtle p-3">
        <input value={actor} onChange={(e) => setActor(e.target.value)} placeholder={t("adm.audit.f_actor")} className={`${inputCls} w-40`} />
        <select value={method} onChange={(e) => setMethod(e.target.value)} className={inputCls}>
          <option value="">{t("adm.audit.f_method_any")}</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="PATCH">PATCH</option>
          <option value="DELETE">DELETE</option>
        </select>
        <input value={path} onChange={(e) => setPath(e.target.value)} placeholder={t("adm.audit.f_path")} className={`${inputCls} w-44`} />
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className={inputCls} title={t("adm.audit.f_date_from")} />
        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className={inputCls} title={t("adm.audit.f_date_to")} />
        <button onClick={load} className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90">{t("adm.audit.apply")}</button>
        <button onClick={reset} className="rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg-muted hover:text-fg">{t("adm.audit.reset")}</button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
        </div>
      ) : items.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">{t("adm.audit.empty")}</div>
      ) : (
        <div className="space-y-2">
          {items.map((it) => (
            <div
              key={it.id}
              className="flex items-center gap-3 rounded-xl border border-border-subtle bg-bg-subtle px-4 py-3"
            >
              <span className={`w-14 shrink-0 text-xs font-bold ${METHOD_COLOR[it.method] ?? "text-fg-muted"}`}>
                {it.method}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-fg">{actionLabel(it.path)}</p>
                <p className="truncate text-xs text-fg-subtle">{it.actor}</p>
              </div>
              <span className="shrink-0 text-xs text-fg-subtle tabular">{fmt(it.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
