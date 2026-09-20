import { useEffect, useState, useCallback } from "react";
import { AlertCircle, ShieldAlert, Wifi, Mail, Users, Ban, TicketX, RefreshCw, Smartphone } from "lucide-react";
import {
  abuseAdminApi,
  usersAdminApi,
  type AbuseCluster,
  type AbuseAccount,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { useAuth } from "@/contexts/AuthContext";
import { useI18n } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { pluralFor } from "@/lib/pluralRu";

// В МЕТА-картах лежат КЛЮЧИ, а не готовые подписи: карта считается один раз при
// импорте модуля, и готовый текст остался бы на языке, который был при загрузке.
const SIGNAL_META: Record<AbuseCluster["signal"], { labelKey: string; icon: typeof Wifi }> = {
  ip: { labelKey: "adm.abuse.signal_ip", icon: Wifi },
  hwid: { labelKey: "adm.abuse.signal_hwid", icon: Smartphone },
  email: { labelKey: "adm.abuse.signal_email", icon: Mail },
  referral: { labelKey: "adm.abuse.signal_referral", icon: Users },
};

const SEVERITY_META: Record<AbuseCluster["severity"], { labelKey: string; cls: string }> = {
  high: { labelKey: "adm.abuse.sev_high", cls: "bg-danger/10 text-danger" },
  medium: { labelKey: "adm.abuse.sev_medium", cls: "bg-warning/10 text-warning" },
  low: { labelKey: "adm.abuse.sev_low", cls: "bg-fg-subtle/20 text-fg-muted" },
};

function accountLabel(a: AbuseAccount): string {
  if (a.username) return `@${a.username}`;
  if (a.email) return a.email;
  if (a.telegram_id) return `tg:${a.telegram_id}`;
  return a.name || `#${a.id}`;
}

export default function AdminAbusePage() {
  const { isReadonlyAdmin } = useAuth();
  const { t, lang } = useI18n();
  const [clusters, setClusters] = useState<AbuseCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [onlyTrial, setOnlyTrial] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  // Чем ищет бэкенд и что у него сейчас не сработало. Оба поля необязательные:
  // их шлёт только бэкенд, у которого набор сигналов не такой, как у нашего.
  const [note, setNote] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    abuseAdminApi
      .trials({ only_trial: onlyTrial })
      .then((r) => {
        setClusters(r.clusters);
        setNote(r.note ?? null);
        setWarning(r.warning ?? null);
      })
      // translate, а не t: иначе t попал бы в зависимости load → useEffect, и смена
      // языка перезапрашивала бы список.
      .catch((e) => setError(e instanceof ApiError ? e.detail : translate("adm.abuse.err_generic")))
      .finally(() => setLoading(false));
  }, [onlyTrial]);

  useEffect(() => { load(); }, [load]);

  // Мутируем счёт локально после действия, чтобы не перезагружать весь список.
  const patchAccount = (id: number, patch: Partial<AbuseAccount>) =>
    setClusters((cs) =>
      cs.map((c) => ({
        ...c,
        accounts: c.accounts.map((a) => (a.id === id ? { ...a, ...patch } : a)),
      })),
    );

  const block = async (a: AbuseAccount) => {
    setBusy(a.id);
    try {
      const r = await usersAdminApi.block(a.id, !a.is_blocked);
      patchAccount(a.id, { is_blocked: r.is_blocked });
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : t("adm.abuse.err_generic"));
    } finally {
      setBusy(null);
    }
  };

  const denyTrial = async (a: AbuseAccount) => {
    setBusy(a.id);
    try {
      const r = await usersAdminApi.setTrial(a.id, !a.is_trial_available);
      patchAccount(a.id, {
        is_trial_available: r.is_trial_available,
        trial_used: !r.is_trial_available,
      });
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : t("adm.abuse.err_generic"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-fg">
          <ShieldAlert className="h-6 w-6 text-warning" />
          {t("adm.abuse.title")}
        </h1>
        <button
          onClick={load}
          className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border)] px-3 py-2 text-sm font-medium text-fg-muted hover:text-fg"
        >
          <RefreshCw className="h-4 w-4" />
          {t("adm.abuse.refresh")}
        </button>
      </div>

      {/* Подсказка своя у каждого бэкенда: сигналы зависят от того, что он вообще
          хранит. Поля `note` нет — текст прежний, наш. */}
      <div className="rounded-2xl border border-border-subtle bg-accent/5 px-5 py-4 text-sm text-fg-muted">
        💡 {note ?? t("adm.abuse.hint")}
      </div>

      {/* Что не сработало сейчас: без этого пустой список читался бы как «чисто». */}
      {warning && (
        <div className="flex items-start gap-2 rounded-xl bg-warning/10 px-4 py-3 text-sm text-warning">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {warning}
        </div>
      )}

      <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          checked={onlyTrial}
          onChange={(e) => setOnlyTrial(e.target.checked)}
          className="h-4 w-4 accent-[var(--accent)]"
        />
        {t("adm.abuse.only_trial")}
      </label>

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
      ) : clusters.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">
          {t("adm.abuse.empty")}
        </div>
      ) : (
        <div className="space-y-4">
          {clusters.map((c, idx) => {
            const meta = SIGNAL_META[c.signal];
            const sev = SEVERITY_META[c.severity];
            const Icon = meta.icon;
            return (
              <div key={`${c.signal}:${c.key}:${idx}`} className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-fg">
                    <Icon className="h-4 w-4 text-fg-muted" />
                    {t(meta.labelKey)}
                  </span>
                  <code className="rounded bg-fg-subtle/10 px-1.5 py-0.5 text-xs text-fg-muted">{c.key}</code>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${sev.cls}`}>{t(sev.labelKey)}</span>
                  <span className="ml-auto text-xs text-fg-subtle">
                    {/* «2 акк.» и «2 accounts»: форму счётного слова выбирает pluralFor ПО ЯЗЫКУ. */}
                    {t(
                      pluralFor(
                        lang,
                        c.accounts.length,
                        "adm.abuse.acc_one",
                        "adm.abuse.acc_few",
                        "adm.abuse.acc_many",
                      ),
                      { n: c.accounts.length },
                    )}
                  </span>
                </div>

                <div className="space-y-1.5">
                  {c.accounts.map((a) => (
                    <div
                      key={a.id}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-[var(--border)] bg-bg px-3 py-2 text-sm"
                    >
                      <span className="font-medium text-fg">{accountLabel(a)}</span>
                      <span className="text-xs text-fg-subtle">#{a.id}</span>
                      {a.trial_used && (
                        <span className="rounded bg-warning/10 px-1.5 text-xs text-warning">{t("adm.abuse.badge_trial_used")}</span>
                      )}
                      {a.young_tg && (
                        <span className="rounded bg-fg-subtle/15 px-1.5 text-xs text-fg-muted">{t("adm.abuse.badge_young_tg")}</span>
                      )}
                      {a.is_blocked && (
                        <span className="rounded bg-danger/10 px-1.5 text-xs text-danger">{t("adm.abuse.badge_blocked")}</span>
                      )}
                      {!isReadonlyAdmin && (
                        <div className="ml-auto flex items-center gap-2">
                          <button
                            onClick={() => denyTrial(a)}
                            disabled={busy === a.id}
                            title={a.is_trial_available ? t("adm.abuse.deny_trial_title") : t("adm.abuse.allow_trial_title")}
                            className="inline-flex items-center gap-1 text-xs font-medium text-fg-muted hover:text-warning disabled:opacity-50"
                          >
                            <TicketX className="h-3.5 w-3.5" />
                            {a.is_trial_available ? t("adm.abuse.deny_trial") : t("adm.abuse.allow_trial")}
                          </button>
                          <button
                            onClick={() => block(a)}
                            disabled={busy === a.id}
                            title={a.is_blocked ? t("adm.abuse.unblock_title") : t("adm.abuse.block_title")}
                            className="inline-flex items-center gap-1 text-xs font-medium text-fg-muted hover:text-danger disabled:opacity-50"
                          >
                            <Ban className="h-3.5 w-3.5" />
                            {a.is_blocked ? t("adm.abuse.unblock") : t("adm.abuse.block")}
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
