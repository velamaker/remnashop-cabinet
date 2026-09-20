import { useEffect, useState, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  Search, ChevronLeft, ChevronRight, AlertCircle, X, Download,
  CalendarPlus, Trash2, Ban, CheckCircle, Gift, RefreshCw, Star, ChevronDown, ChevronUp, LogIn, Gauge, Wallet, Link2, Users,
} from "lucide-react";
import {
  usersAdminApi, subscriptionsAdminApi, plansAdminApi, grantsAdminApi,
  type AdminUser, type AdminUserDetail, type AdminSubscription, type AdminPlan,
  type LoginHistory, type TrafficByNode, type GrantCatalog, type GrantPreset, type UserGrant,
  type AdminDevice, type AdminUserTx, type AdminSquadsResponse,
  type UserReferrals, type ReferralMember,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate, formatRelativeOnline } from "@/lib/format";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { useAuth } from "@/contexts/AuthContext";
import { useBranding } from "@/contexts/BrandingContext";
import { BulkDaysDialog, BulkJobsPanel, BulkMessageDialog } from "./AdminUsersBulk";

const LIMIT = 25;

// ─── Что этот бэкенд умеет на самом деле ───────────────────────────────────
//
// Кабинет один, а бэкенд под ним может быть разный: наш RemnaShop умеет всё, а
// поверх чужого бота («Бедолага» и дальше) работает адаптер, у которого
// переведена только часть админских ручек. Кнопка, за которой нет ручки, —
// худший вид интерфейса: человек жмёт, ждёт и получает «Этот раздел недоступен».
// Поэтому такие органы управления надо ПРЯТАТЬ, а не показывать.
//
// Источников три, и все они уже приходят в кабинет:
//   1) whoami.can_write (AuthContext.isReadonlyAdmin) — можно ли вообще менять
//      данные. Нельзя — не рисуем ни одной кнопки действия;
//   2) whoami.sections (AuthContext.canSection) — какие разделы бэкенд отдаёт
//      целиком. Отсюда вкладка «Подписка» (раздел subscriptions) и карточка
//      «Выдать подписку» (ей нужен список тарифов, раздел plans);
//   3) appearance.features (BrandingContext.can) — какие механики есть у бота
//      вообще. Баллов лояльности, например, у «Бедолаги» нет как понятия.
//
// ГЛАВНОЕ ПРАВИЛО СОВМЕСТИМОСТИ. На нашем бэкенде `features` нет вовсе,
// full_access=true, can_write=true — и все три проверки отвечают «можно», то
// есть страница остаётся ровно такой, как была. Ни одна проверка ниже не
// написана как «=== true»: отсутствие сведений всегда читается как «умеет».
//
// ЧЕГО ЭТИ ТРИ ИСТОЧНИКА НЕ ЗНАЮТ. Права нарезаны разделами, а не отдельными
// ручками: раздел «users» бэкенд объявляет целиком, хотя внутри у него может не
// быть, скажем, сброса трафика. Единственный, кто знает правду про конкретную
// ручку, — сам бэкенд, и он её честно говорит: 501 «не умею». Этот ответ мы
// запоминаем (см. `UNSUPPORTED`) и после него орган управления убираем — и в
// этой карточке, и во всех следующих. Наш бэкенд 501 не отвечает никогда,
// поэтому у нас реестр остаётся пустым.

/** Ручки, про которые бэкенд уже ответил «не умею» (HTTP 501).
 *
 *  Модульный, а не в состоянии компонента: карточка пользователя открывается и
 *  закрывается десятки раз за сессию, и заново натыкаться на ту же мёртвую
 *  кнопку в каждой — ровно то, от чего мы уходим. Сбрасывается перезагрузкой
 *  страницы: если раздел на бэкенде появится, админ увидит его после F5. */
const UNSUPPORTED = new Set<string>();

/** Тот самый честный отказ «такой ручки тут нет». Всё остальное (403, 500,
 *  обрыв сети) — не повод прятать кнопку: это временная беда, а не отсутствие
 *  возможности. */
function isUnsupported(e: unknown): boolean {
  return e instanceof ApiError && e.status === 501;
}

/** Доступ к реестру возможностей с перерисовкой при новой находке.
 *
 *  `note` возвращает true, если ошибка означала «бэкенд так не умеет» — по нему
 *  вызывающий код решает, показывать ли текст ошибки (уже незачем: орган
 *  управления исчезнет) или откатывать фильтр. */
function useCapabilities() {
  const [, bump] = useState(0);
  const disable = useCallback((key: string) => { UNSUPPORTED.add(key); bump(n => n + 1); }, []);
  const note = useCallback((key: string, e: unknown) => {
    if (!isUnsupported(e)) return false;
    disable(key);
    return true;
  }, [disable]);
  return { can: (key: string) => !UNSUPPORTED.has(key), note, disable };
}

/** Экспорт качается сырым fetch (нужен файл, а не JSON), поэтому ApiError до
 *  страницы не доезжает — статус приходит только текстом сообщения. Разбираем
 *  его, чтобы отличить «такого пути на этом бэкенде нет» от временной поломки:
 *  501 — прямой отказ адаптера, а 404/405/415/422 означают, что бэкенд про этот
 *  адрес не знает (у адаптера `/users/export.xlsx` попадает в маршрут карточки
 *  `/users/{id}` и отвечает 422 «это не число»). 5xx кнопку не прячет: сервер
 *  мог просто споткнуться. */
const NO_EXPORT_ROUTE = /\(HTTP (?:404|405|415|422|501)\)/;

// Значения совпадают с серверным enum Role: USER=1, PREVIEW=2 (read-only админ),
// ADMIN=3, DEV=4, OWNER=5, SYSTEM=6.
const ROLE_LABELS: Record<number, { key: string; cls: string }> = {
  1: { key: "adm.users.role_user", cls: "text-fg-muted" },
  2: { key: "adm.users.role_admin_view", cls: "text-warning" },
  3: { key: "adm.users.role_admin", cls: "text-warning" },
  4: { key: "adm.users.role_dev", cls: "text-accent" },
  5: { key: "adm.users.role_owner", cls: "text-accent" },
  6: { key: "adm.users.role_system", cls: "text-accent" },
};

/** Подпись роли на языке кабинета: в словаре ролей лежит ключ перевода, а не
 *  готовый текст. Неизвестную роль показываем номером (в списке — просто
 *  числом, там для слова «роль» нет места). */
function roleInfo(role: number, fallback?: string): { label: string; cls: string } {
  const meta = ROLE_LABELS[role];
  if (meta) return { label: translate(meta.key), cls: meta.cls };
  return { label: fallback ?? translate("adm.users.role_n", { n: role }), cls: "text-fg-muted" };
}


const STATUS_COLORS: Record<string, string> = {
  ACTIVE: "text-success",
  EXPIRED: "text-danger",
  DISABLED: "text-fg-muted",
  DELETED: "text-fg-subtle",
  LIMITED: "text-warning",
};

function Tag({ children, cls }: { children: React.ReactNode; cls?: string }) {
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ${cls ?? "bg-bg-raised text-fg-muted border border-[var(--border)]"}`}>
      {children}
    </span>
  );
}

// ─── Subscription Panel ────────────────────────────────────────────────────

function SubscriptionPanel({ userId, points, balance, onUpdated }: { userId: number; points: number; balance: number; onUpdated: () => void }) {
  const [data, setData] = useState<{ current: AdminSubscription | null; history: AdminSubscription[] } | null>(null);
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<string | null>(null);
  const [extendDays, setExtendDays] = useState("30");
  const [grantPlanId, setGrantPlanId] = useState("");
  const [grantDays, setGrantDays] = useState("30");
  const [pointsDelta, setPointsDelta] = useState("0");
  const [balanceDelta, setBalanceDelta] = useState("0");
  const [showHistory, setShowHistory] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const t = useT();
  const { isReadonlyAdmin, canSection } = useAuth();
  const { can: hasFeature } = useBranding();
  const { can, note } = useCapabilities();

  // Тарифы нужны ровно одной карточке — «Выдать подписку». Раздел «plans»
  // бэкенд объявляет сам, и спрашивать список, когда раздела нет, нельзя: он
  // грузится тем же Promise.all, и его 501 показал бы ошибку вместо ВСЕЙ
  // вкладки, хотя сама подписка читается прекрасно.
  const canPlans = canSection("plans");

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      subscriptionsAdminApi.getUser(userId),
      canPlans ? plansAdminApi.list() : Promise.resolve({ items: [] as AdminPlan[], total: 0 }),
    ]).then(([sub, p]) => {
      setData(sub);
      setPlans(p.items);
      if (p.items[0]) setGrantPlanId(String(p.items[0].id));
    }).catch(e => setErr(e instanceof ApiError ? e.detail : t("adm.users.err_generic"))).finally(() => setLoading(false));
  }, [userId, canPlans, t]);

  useEffect(() => { load(); }, [load]);

  // `cap` — ключ возможности этого действия. Бэкенд ответил «не умею» — кнопка
  // исчезает, и текст ошибки не показываем: он объяснял бы уже пустое место.
  const run = async (fn: () => Promise<unknown>, label: string, cap?: string) => {
    setAction(label);
    setErr(null);
    try {
      await fn();
      load();
      onUpdated();
    } catch (e) {
      if (cap && note(cap, e)) return;
      setErr(e instanceof ApiError ? e.detail : t("adm.users.err_generic"));
    } finally {
      setAction(null);
    }
  };

  const sub = data?.current;
  // Баллы лояльности — механика бота, а не кабинета: нет её у бэкенда, и
  // начислять нечего (в карточке они всегда 0).
  const canPoints = hasFeature("points") && can("sub.points");
  // Карточки обслуживания и опасных действий: рисуем только те кнопки, за
  // которыми есть ручка, а сами карточки — только если хоть одна кнопка жива.
  const upkeep = ["sub.reset-traffic", "sub.reissue", "sub.referral-reset", "sub.sync"].filter(can);
  const danger = ["sub.reset-trial", "sub.disable", "sub.delete"].filter(can);
  const actionCards = [
    can("sub.extend"), canPlans && can("sub.grant"), canPoints, can("sub.balance"),
    upkeep.length > 0, danger.length > 0,
  ].filter(Boolean).length;

  if (loading) return <div className="flex justify-center py-6"><div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" /></div>;

  return (
    <div className="space-y-4">
      {err && <p className="rounded-lg bg-danger/8 px-3 py-2 text-xs text-danger">{err}</p>}

      {/* Current subscription */}
      <div className="rounded-xl border border-[var(--border)] bg-bg-raised p-4">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-fg-subtle">{t("adm.users.sub_current")}</p>
        {sub ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-fg">{sub.plan_name ?? "—"}</span>
              <span className={`text-xs font-medium ${STATUS_COLORS[sub.status] ?? "text-fg-muted"}`}>{sub.status}</span>
            </div>
            {sub.expire_at && (
              <p className="text-xs text-fg-muted">
                {t("adm.users.sub_expires_label")} <span className="text-fg">{formatDate(sub.expire_at)}</span>
              </p>
            )}
            <div className="flex gap-3 text-xs text-fg-muted">
              {/* traffic_limit приходит в ГБ (в байтах его хранит только панель). */}
              <span>{t("adm.users.sub_traffic", { v: sub.traffic_limit === 0 ? "∞" : t("adm.users.gb_value", { n: sub.traffic_limit }) })}</span>
              <span>
                {t("adm.users.sub_devices", { v: sub.device_limit === 0 ? "∞" : sub.device_limit })}
                {/* Расшифровка нужна, чтобы «4» не выглядело как навсегда: одно из
                    мест докуплено и в свой срок отвалится. */}
                {!!sub.extra_devices_active && sub.plan_device_limit != null && (
                  <span className="text-fg-subtle">
                    {" "}
                    {sub.extra_until
                      ? t("adm.users.sub_devices_extra_until", { plan: sub.plan_device_limit, extra: sub.extra_devices_active, d: formatDate(sub.extra_until) })
                      : t("adm.users.sub_devices_extra", { plan: sub.plan_device_limit, extra: sub.extra_devices_active })}
                  </span>
                )}
              </span>
              {sub.is_trial && <Tag cls="bg-accent/8 text-accent border-accent/15">{t("adm.users.sub_trial_tag")}</Tag>}
            </div>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">{t("adm.users.sub_none")}</p>
        )}
      </div>

      {/* Actions. Read-only админу их не рисуем вовсе (флаг can_write), а из
          остальных показываем только те, под которыми у бэкенда есть ручка. */}
      {!isReadonlyAdmin && actionCards > 0 && (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Extend */}
        {can("sub.extend") && (
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg"><CalendarPlus className="h-3.5 w-3.5 text-success" />{t("adm.users.extend_title")}</p>
          <div className="flex gap-2">
            <input type="number" min={-3650} max={3650} value={extendDays} onChange={e => setExtendDays(e.target.value)}
              placeholder={t("adm.users.extend_ph")}
              className="h-8 w-20 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
            <span className="self-center text-xs text-fg-muted">{t("adm.users.days_word")}</span>
            <button
              onClick={() => run(() => subscriptionsAdminApi.extend(userId, Number(extendDays)), "extend", "sub.extend")}
              disabled={action !== null || !sub || !extendDays || Number(extendDays) === 0}
              className="ml-auto rounded-lg bg-success/10 px-3 py-1.5 text-xs font-medium text-success hover:bg-success/20 disabled:opacity-40 transition-colors"
            >
              {action === "extend" ? "…" : t("adm.users.btn_apply")}
            </button>
          </div>
          <p className="mt-1.5 text-[11px] text-fg-subtle">{t("adm.users.extend_hint")}</p>
        </div>
        )}

        {/* Grant. Без списка тарифов выдавать нечего — карточка живёт вместе с
            разделом «Тарифы». */}
        {canPlans && can("sub.grant") && (
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg"><Gift className="h-3.5 w-3.5 text-accent" />{t("adm.users.grant_sub_title")}</p>
          <div className="flex flex-col gap-2">
            <select value={grantPlanId} onChange={e => setGrantPlanId(e.target.value)}
              className="h-8 rounded-lg border border-[var(--border)] bg-bg px-2 text-xs text-fg focus:outline-none focus:ring-1 focus:ring-accent">
              {plans.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <div className="flex gap-2">
              <input type="number" min={1} max={3650} value={grantDays} onChange={e => setGrantDays(e.target.value)}
                className="h-8 w-20 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
              <span className="self-center text-xs text-fg-muted">{t("adm.users.days_word")}</span>
              <button
                onClick={() => run(() => subscriptionsAdminApi.grant(userId, Number(grantPlanId), Number(grantDays)), "grant", "sub.grant")}
                disabled={action !== null || !grantPlanId}
                className="ml-auto rounded-lg bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 disabled:opacity-40 transition-colors"
              >
                {action === "grant" ? "…" : t("adm.users.btn_grant_sub")}
              </button>
            </div>
          </div>
        </div>
        )}

        {/* Points */}
        {canPoints && (
        <div className="rounded-xl border border-[var(--border)] p-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-fg"><Star className="h-3.5 w-3.5 text-warning" />{t("adm.users.points_title")}</p>
            <span className="text-sm font-bold text-fg">
              {points}
              {Number(pointsDelta) !== 0 && !Number.isNaN(Number(pointsDelta)) && (
                <span className="ml-1 text-xs font-medium text-fg-muted">→ {Math.max(0, points + Number(pointsDelta))}</span>
              )}
            </span>
          </div>
          <div className="flex gap-2">
            <input type="number" value={pointsDelta} onChange={e => setPointsDelta(e.target.value)}
              className="h-8 w-24 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
              placeholder={t("adm.users.points_ph")} />
            <button
              onClick={() => run(() => subscriptionsAdminApi.addPoints(userId, Number(pointsDelta)), "points", "sub.points")}
              disabled={action !== null || !pointsDelta || Number(pointsDelta) === 0}
              className="ml-auto rounded-lg bg-warning/10 px-3 py-1.5 text-xs font-medium text-warning hover:bg-warning/20 disabled:opacity-40 transition-colors"
            >
              {action === "points" ? "…" : Number(pointsDelta) < 0 ? t("adm.users.btn_debit") : t("adm.users.btn_credit")}
            </button>
          </div>
        </div>
        )}

        {/* Balance (₽) */}
        {can("sub.balance") && (
        <div className="rounded-xl border border-[var(--border)] p-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-fg"><Wallet className="h-3.5 w-3.5 text-accent" />{t("adm.users.balance_title")}</p>
            <span className="text-sm font-bold text-fg">
              {balance.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽
              {Number(balanceDelta) !== 0 && !Number.isNaN(Number(balanceDelta)) && (
                <span className="ml-1 text-xs font-medium text-fg-muted">→ {Math.max(0, balance + Number(balanceDelta)).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽</span>
              )}
            </span>
          </div>
          <div className="flex gap-2">
            <input type="number" value={balanceDelta} onChange={e => setBalanceDelta(e.target.value)}
              className="h-8 w-24 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
              placeholder={t("adm.users.balance_ph")} />
            <button
              onClick={() => run(() => subscriptionsAdminApi.adjustBalance(userId, Number(balanceDelta)), "balance", "sub.balance")}
              disabled={action !== null || !balanceDelta || Number(balanceDelta) === 0}
              className="ml-auto rounded-lg bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 disabled:opacity-40 transition-colors"
            >
              {action === "balance" ? "…" : Number(balanceDelta) < 0 ? t("adm.users.btn_debit") : t("adm.users.btn_credit")}
            </button>
          </div>
          <p className="mt-1.5 text-[11px] text-fg-subtle">{t("adm.users.balance_hint")}</p>
        </div>
        )}

        {/* Обслуживание подписки (паритет с ботом) */}
        {upkeep.length > 0 && (
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 text-xs font-semibold text-fg-subtle">{t("adm.users.upkeep_title")}</p>
          <div className="flex flex-wrap gap-2">
            {can("sub.reset-traffic") && (
            <button
              onClick={() => run(() => subscriptionsAdminApi.resetTraffic(userId), "traffic", "sub.reset-traffic")}
              disabled={action !== null || !sub}
              className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-bg-raised disabled:opacity-40 transition-colors"
            >
              <Gauge className="h-3 w-3" />{action === "traffic" ? "…" : t("adm.users.btn_reset_traffic")}
            </button>
            )}
            {can("sub.reissue") && (
            <button
              onClick={() => { if (confirm(t("adm.users.confirm_reissue"))) run(() => subscriptionsAdminApi.reissue(userId), "reissue", "sub.reissue"); }}
              disabled={action !== null || !sub}
              className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-bg-raised disabled:opacity-40 transition-colors"
            >
              <Link2 className="h-3 w-3" />{action === "reissue" ? "…" : t("adm.users.btn_reissue")}
            </button>
            )}
            {can("sub.referral-reset") && (
            <button
              onClick={() => { if (confirm(t("adm.users.confirm_referral_reset"))) run(() => subscriptionsAdminApi.referralReset(userId), "refreset", "sub.referral-reset"); }}
              disabled={action !== null}
              className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-bg-raised disabled:opacity-40 transition-colors"
            >
              <Gift className="h-3 w-3" />{action === "refreset" ? "…" : t("adm.users.btn_referral_reset")}
            </button>
            )}
            {can("sub.sync") && (
            <button
              onClick={() => run(() => subscriptionsAdminApi.sync(userId, "from_remnawave"), "sync", "sub.sync")}
              disabled={action !== null || !sub}
              title={t("adm.users.sync_hint")}
              className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-bg-raised disabled:opacity-40 transition-colors"
            >
              <RefreshCw className="h-3 w-3" />{action === "sync" ? "…" : t("adm.users.btn_sync")}
            </button>
            )}
          </div>
        </div>
        )}

        {/* Danger actions */}
        {danger.length > 0 && (
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 text-xs font-semibold text-fg-subtle">{t("adm.users.danger_title")}</p>
          <div className="flex flex-wrap gap-2">
            {can("sub.reset-trial") && (
            <button
              onClick={() => run(() => subscriptionsAdminApi.resetTrial(userId), "trial", "sub.reset-trial")}
              disabled={action !== null}
              className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-bg-raised disabled:opacity-40 transition-colors"
            >
              <RefreshCw className="h-3 w-3" />{action === "trial" ? "…" : t("adm.users.btn_reset_trial")}
            </button>
            )}
            {can("sub.disable") && (
            <button
              onClick={() => run(() => subscriptionsAdminApi.disable(userId), "disable", "sub.disable")}
              disabled={action !== null || !sub}
              className="flex items-center gap-1 rounded-lg border border-warning/20 bg-warning/8 px-2.5 py-1.5 text-xs text-warning hover:bg-warning/15 disabled:opacity-40 transition-colors"
            >
              <Ban className="h-3 w-3" />{action === "disable" ? "…" : t("adm.users.btn_disable")}
            </button>
            )}
            {can("sub.delete") && (
            <button
              onClick={() => { if (confirm(t("adm.users.confirm_delete_sub"))) run(() => subscriptionsAdminApi.delete(userId), "delete", "sub.delete"); }}
              disabled={action !== null || !sub}
              className="flex items-center gap-1 rounded-lg border border-danger/20 bg-danger/8 px-2.5 py-1.5 text-xs text-danger hover:bg-danger/15 disabled:opacity-40 transition-colors"
            >
              <Trash2 className="h-3 w-3" />{action === "delete" ? "…" : t("adm.users.btn_delete")}
            </button>
            )}
          </div>
        </div>
        )}
      </div>
      )}

      {/* History */}
      {(data?.history?.length ?? 0) > 0 && (
        <div>
          <button onClick={() => setShowHistory(!showHistory)} className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg transition-colors">
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showHistory ? "rotate-180" : ""}`} />
            {t("adm.users.sub_history_n", { n: data!.history.length })}
          </button>
          {showHistory && (
            <div className="mt-2 space-y-1">
              {data!.history.map(s => (
                <div key={s.id} className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2 text-xs">
                  <span className="text-fg-muted">{s.plan_name ?? "—"}</span>
                  <span className={STATUS_COLORS[s.status] ?? "text-fg-muted"}>{s.status}</span>
                  {s.expire_at && <span className="text-fg-subtle">{formatDate(s.expire_at)}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Limits & squads. Блок целиком состоит из изменений — read-only админу
          показывать нечего. */}
      {sub && !isReadonlyAdmin && <LimitsAndSquadsBlock userId={userId} sub={sub} onUpdated={() => { load(); onUpdated(); }} />}

      {/* Devices */}
      <DevicesBlock userId={userId} />

      {/* Transactions */}
      <UserTxBlock userId={userId} />

      {/* Send message */}
      <SendMessageBlock userId={userId} />
    </div>
  );
}

// ─── Устройства пользователя ─────────────────────────────────────────────────

function DevicesBlock({ userId }: { userId: number }) {
  const [open, setOpen] = useState(false);
  const [devices, setDevices] = useState<AdminDevice[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const t = useT();
  const { isReadonlyAdmin } = useAuth();
  const { can, note } = useCapabilities();

  // Раньше любая ошибка превращалась в пустой список, и блок писал «Устройств
  // нет» — на бэкенде, который про устройства не знает вовсе, это выдуманный
  // факт: админ читал его как «человек не подключался».
  const load = () =>
    subscriptionsAdminApi.devices(userId)
      .then((r) => setDevices(r.devices))
      .catch((e) => { note("sub.devices", e); setDevices([]); });
  const toggle = () => { const n = !open; setOpen(n); if (n && devices === null) load(); };
  const del = async (hwid: string) => {
    if (!confirm(t("adm.users.confirm_delete_device"))) return;
    setBusy(hwid);
    try { await subscriptionsAdminApi.deleteDevice(userId, hwid); await load(); }
    catch (e) { note("sub.devices.delete", e); /* 403 у read-only — не наш случай */ }
    finally { setBusy(null); }
  };

  if (!can("sub.devices")) return null;

  return (
    <div>
      <button onClick={toggle} className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg transition-colors">
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        {devices ? t("adm.users.devices_title_n", { n: devices.length }) : t("adm.users.devices_title")}
      </button>
      {open && (
        <div className="mt-2 space-y-1">
          {devices === null ? (
            <p className="text-xs text-fg-subtle">{t("adm.users.loading")}</p>
          ) : devices.length === 0 ? (
            <p className="text-xs text-fg-subtle">{t("adm.users.devices_empty")}</p>
          ) : devices.map((d) => (
            <div key={d.hwid} className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-xs">
              <div className="min-w-0 flex-1">
                <p className="truncate text-fg">{d.platform || "—"}{d.device_model ? ` · ${d.device_model}` : ""}</p>
                <p className="truncate text-fg-subtle">{d.os_version || d.user_agent || d.hwid}</p>
                {/* Когда устройство подключилось и когда заходило в последний раз:
                    без этого в списке из пяти одинаковых айфонов невозможно понять,
                    какой из них лишний и какой пора удалить. */}
                {(d.created_at || d.updated_at) && (
                  <p className="truncate text-fg-subtle">
                    {d.created_at && <>{t("adm.users.device_connected", { d: formatDate(d.created_at) })}</>}
                    {d.created_at && d.updated_at && " · "}
                    {d.updated_at && <>{t("adm.users.device_active", { d: formatRelativeOnline(d.updated_at) })}</>}
                  </p>
                )}
              </div>
              {!isReadonlyAdmin && can("sub.devices.delete") && (
              <button
                onClick={() => del(d.hwid)}
                disabled={busy !== null}
                className="flex shrink-0 items-center gap-1 rounded-lg border border-danger/20 bg-danger/8 px-2 py-1 text-danger hover:bg-danger/15 disabled:opacity-40 transition-colors"
              >
                <Trash2 className="h-3 w-3" />{busy === d.hwid ? "…" : t("adm.users.btn_delete")}
              </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Платежи пользователя ────────────────────────────────────────────────────

function UserTxBlock({ userId }: { userId: number }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AdminUserTx[] | null>(null);
  const t = useT();
  const { can, note } = useCapabilities();

  const toggle = () => {
    const n = !open;
    setOpen(n);
    if (n && items === null) {
      // Как и с устройствами: «Платежей нет» на бэкенде без такой ручки — не
      // пустой результат, а неправда.
      subscriptionsAdminApi.transactions(userId)
        .then((r) => setItems(r.items))
        .catch((e) => { note("sub.transactions", e); setItems([]); });
    }
  };

  if (!can("sub.transactions")) return null;

  return (
    <div>
      <button onClick={toggle} className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg transition-colors">
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        {items ? t("adm.users.tx_title_n", { n: items.length }) : t("adm.users.tx_title")}
      </button>
      {open && (
        <div className="mt-2 space-y-1">
          {items === null ? (
            <p className="text-xs text-fg-subtle">{t("adm.users.loading")}</p>
          ) : items.length === 0 ? (
            <p className="text-xs text-fg-subtle">{t("adm.users.tx_empty")}</p>
          ) : items.map((tx) => (
            <div key={tx.payment_id} className="flex items-center justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-fg-muted">
                {tx.plan_name ?? tx.purchase_type ?? "—"}{tx.is_test ? ` · ${t("adm.users.tx_test")}` : ""}
              </span>
              <span className="shrink-0 text-fg">{tx.amount ? `${tx.amount} ${tx.currency ?? ""}` : "—"}</span>
              <span className={`shrink-0 ${tx.status === "COMPLETED" ? "text-success" : tx.status === "FAILED" || tx.status === "CANCELED" ? "text-danger" : "text-fg-subtle"}`}>
                {tx.status}
              </span>
              {tx.created_at && <span className="shrink-0 text-fg-subtle">{formatDate(tx.created_at)}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Лимиты и серверы (сквады) ───────────────────────────────────────────────

function LimitsAndSquadsBlock({ userId, sub, onUpdated }: { userId: number; sub: AdminSubscription; onUpdated: () => void }) {
  const [open, setOpen] = useState(false);
  const [traffic, setTraffic] = useState(String(sub.traffic_limit ?? 0));
  const [devices, setDevices] = useState(String(sub.device_limit ?? 0));
  const [squads, setSquads] = useState<AdminSquadsResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  // Текст 409 от бэкенда: «Докуплено +N устр. до ДД.ММ. Отменить докупку и поставить M?»
  const [extrasConflict, setExtrasConflict] = useState<string | null>(null);
  const t = useT();
  const { can, note } = useCapabilities();

  const toggle = () => {
    const n = !open;
    setOpen(n);
    if (n && squads === null) {
      plansAdminApi.squads().then(setSquads).catch(() => setSquads({ internal: [], external: [], available: false }));
    }
  };

  // `cap` — ключ возможности: бэкенд ответил «не умею», и строка (или чипы
  // серверов) уходит с экрана вместе с сообщением об ошибке.
  const act = async (key: string, fn: () => Promise<unknown>, cap: string) => {
    setBusy(key);
    setMsg(null);
    try { await fn(); setMsg(t("adm.users.saved")); onUpdated(); }
    catch (e) {
      if (!note(cap, e)) setMsg(e instanceof ApiError ? e.detail : t("adm.users.err_generic"));
    }
    finally { setBusy(null); }
  };

  /**
   * Лимит устройств. Ниже «тариф + докупленные» бэкенд отвечает 409 с объяснением:
   * молча отнять оплаченное место нельзя. Подтверждение админа уходит вторым
   * запросом с revoke_extras — тогда места помечаются отменёнными явно.
   */
  const setDeviceLimit = async (revokeExtras: boolean) => {
    const value = Math.max(0, parseInt(devices, 10) || 0);
    setBusy("devices");
    setMsg(null);
    setExtrasConflict(null);
    try {
      await subscriptionsAdminApi.setDeviceLimit(userId, value, revokeExtras);
      setMsg(t("adm.users.saved"));
      onUpdated();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setExtrasConflict(e.detail);
      } else if (!note("sub.device-limit", e)) {
        setMsg(e instanceof ApiError ? e.detail : t("adm.users.err_generic"));
      }
    } finally {
      setBusy(null);
    }
  };

  const squadChip = (uuid: string, name: string, on: boolean, external: boolean) => (
    <button
      key={uuid}
      disabled={busy !== null}
      onClick={() => act((external ? "ex-" : "sq-") + uuid, () => subscriptionsAdminApi.squadToggle(userId, uuid, external), "sub.squad-toggle")}
      className={`rounded-lg border px-2 py-1 text-xs transition-colors disabled:opacity-40 ${on ? "border-accent/40 bg-accent/10 text-accent" : "border-[var(--border)] text-fg-muted hover:text-fg"}`}
    >
      {on ? "✓ " : ""}{name}
    </button>
  );

  // Блок — это три изменяющие ручки и больше ничего. Не осталось ни одной живой
  // — заголовок «Лимиты и серверы» открывал бы пустую коробку.
  const canTraffic = can("sub.traffic-limit");
  const canDevices = can("sub.device-limit");
  const canSquads = can("sub.squad-toggle");
  if (!canTraffic && !canDevices && !canSquads) return null;

  return (
    <div>
      <button onClick={toggle} className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg transition-colors">
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        {t("adm.users.limits_title")}
      </button>
      {open && (
        <div className="mt-2 space-y-3 rounded-xl border border-[var(--border)] p-3">
          {msg && <p className="text-xs text-fg-subtle">{msg}</p>}
          {canTraffic && (
          <div className="flex items-center gap-2">
            <label className="w-24 text-xs text-fg-muted">{t("adm.users.limit_traffic_label")}</label>
            <input type="number" min={0} value={traffic} onChange={(e) => setTraffic(e.target.value)}
              className="w-24 rounded-lg border border-[var(--border)] bg-bg px-2 py-1 text-xs text-fg" />
            <span className="text-[10px] text-fg-subtle">{t("adm.users.zero_is_inf")}</span>
            <button onClick={() => act("traffic", () => subscriptionsAdminApi.setTrafficLimit(userId, Math.max(0, parseInt(traffic, 10) || 0)), "sub.traffic-limit")}
              disabled={busy !== null}
              className="ml-auto rounded-lg bg-accent/10 px-3 py-1 text-xs text-accent hover:bg-accent/20 disabled:opacity-40">
              {busy === "traffic" ? "…" : "OK"}
            </button>
          </div>
          )}
          {canDevices && (
          <div className="flex items-center gap-2">
            <label className="w-24 text-xs text-fg-muted">{t("adm.users.devices_title")}</label>
            <input type="number" min={0} value={devices} onChange={(e) => setDevices(e.target.value)}
              className="w-24 rounded-lg border border-[var(--border)] bg-bg px-2 py-1 text-xs text-fg" />
            <span className="text-[10px] text-fg-subtle">{t("adm.users.zero_is_inf")}</span>
            <button onClick={() => setDeviceLimit(false)}
              disabled={busy !== null}
              className="ml-auto rounded-lg bg-accent/10 px-3 py-1 text-xs text-accent hover:bg-accent/20 disabled:opacity-40">
              {busy === "devices" ? "…" : "OK"}
            </button>
          </div>
          )}
          {extrasConflict && (
            <div className="rounded-lg border border-warning/40 bg-warning/10 p-2 text-xs text-fg">
              <p>{extrasConflict}</p>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => setDeviceLimit(true)}
                  disabled={busy !== null}
                  className="rounded-lg bg-warning/20 px-2 py-1 text-xs text-fg hover:bg-warning/30 disabled:opacity-40"
                >
                  {t("adm.users.extras_revoke_btn")}
                </button>
                <button
                  onClick={() => setExtrasConflict(null)}
                  className="rounded-lg px-2 py-1 text-xs text-fg-muted hover:text-fg"
                >
                  {t("adm.users.btn_cancel")}
                </button>
              </div>
            </div>
          )}
          {canSquads && squads && squads.internal.length > 0 && (
            <div>
              <p className="mb-1 text-xs text-fg-muted">{t("adm.users.squads_internal")}</p>
              <div className="flex flex-wrap gap-1.5">
                {squads.internal.map((s) => squadChip(s.uuid, s.name, sub.internal_squads.includes(s.uuid), false))}
              </div>
            </div>
          )}
          {canSquads && squads && squads.external.length > 0 && (
            <div>
              <p className="mb-1 text-xs text-fg-muted">{t("adm.users.squads_external")}</p>
              <div className="flex flex-wrap gap-1.5">
                {squads.external.map((s) => squadChip(s.uuid, s.name, sub.external_squad === s.uuid, true))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Сообщение пользователю ──────────────────────────────────────────────────

function SendMessageBlock({ userId }: { userId: number }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const t = useT();
  const { isReadonlyAdmin } = useAuth();
  const { can, note } = useCapabilities();

  const send = async () => {
    const body = text.trim();
    if (!body) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await subscriptionsAdminApi.sendMessage(userId, body);
      // Причина приходит с сервера: раньше любая неудача объявлялась «нет Telegram»,
      // хотя профиль мог быть с привязкой, а отправку срывала блокировка бота.
      setMsg(
        r.delivered
          ? t("adm.users.msg_sent")
          : r.reason === "no_telegram"
            ? t("adm.users.msg_no_telegram")
            : t("adm.users.msg_bot_blocked"),
      );
      if (r.delivered) setText("");
    } catch (e) {
      if (!note("sub.message", e)) setMsg(e instanceof ApiError ? e.detail : t("adm.users.err_generic"));
    } finally {
      setBusy(false);
    }
  };

  // Отправка — единственное, что здесь есть: нет ручки (или админ только
  // смотрит) — нет и блока.
  if (isReadonlyAdmin || !can("sub.message")) return null;

  return (
    <div>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg transition-colors">
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        {t("adm.users.message_title")}
      </button>
      {open && (
        <div className="mt-2 space-y-2 rounded-xl border border-[var(--border)] p-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            placeholder={t("adm.users.message_ph")}
            className="w-full rounded-lg border border-[var(--border)] bg-bg px-2 py-1.5 text-xs text-fg"
          />
          <div className="flex items-center gap-2">
            {msg && <span className="text-xs text-fg-subtle">{msg}</span>}
            <button
              onClick={send}
              disabled={busy || !text.trim()}
              className="ml-auto rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
            >
              {busy ? "…" : t("adm.users.btn_send")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Login History ─────────────────────────────────────────────────────────

function LoginHistoryBlock({ userId }: { userId: number }) {
  const [data, setData] = useState<LoginHistory | null>(null);
  const [open, setOpen] = useState(false);
  const t = useT();

  useEffect(() => {
    usersAdminApi.logins(userId).then(setData).catch(() => setData(null));
  }, [userId]);

  if (!data || data.total === 0) return null;

  const methodLabel = (m: string | null) =>
    m === "telegram_oidc" ? "Telegram" :
    m === "telegram_webapp" ? "Telegram Mini App" :
    m === "telegram" ? "Telegram" :
    m === "register" ? t("adm.users.login_method_register") :
    m === "email" ? "Email" : (m ?? "—");

  return (
    <div className="rounded-xl border border-[var(--border)] p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-2 text-xs font-semibold text-fg">
        <span className="flex items-center gap-1.5"><LogIn className="h-3.5 w-3.5 text-accent" />{t("adm.users.logins_title")}</span>
        <span className="font-normal text-fg-subtle">
          {t("adm.users.logins_summary", { n: data.total, ips: data.distinct_ips })}
          <ChevronDown className={`ml-1 inline h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-1">
          {data.items.map((e, i) => (
            <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-xs">
              <span className="text-fg-muted">{e.created_at ? formatDate(e.created_at) : "—"}</span>
              <span className="text-fg-subtle">{methodLabel(e.method)}</span>
              <span className="font-mono text-fg">{e.ip ?? t("adm.users.ip_hidden")}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Referrals (реф-связи) ─────────────────────────────────────────────────

function ReferralsBlock({ userId, onOpenUser }: { userId: number; onOpenUser?: (id: number) => void }) {
  const [data, setData] = useState<UserReferrals | null>(null);
  const [open, setOpen] = useState(false);
  const t = useT();

  useEffect(() => {
    usersAdminApi.referrals(userId).then(setData).catch(() => setData(null));
  }, [userId]);

  if (!data || (!data.referrer && data.counts.first === 0 && data.counts.second === 0)) return null;

  const Member = ({ m }: { m: ReferralMember }) => (
    <button
      type="button"
      onClick={onOpenUser ? () => onOpenUser(m.id) : undefined}
      disabled={!onOpenUser}
      className="flex w-full items-center justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-xs transition-colors enabled:hover:bg-bg-subtle disabled:cursor-default"
    >
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate font-medium text-fg">{m.name}</span>
        {m.username && <span className="truncate text-fg-subtle">@{m.username}</span>}
      </span>
      <span className="flex flex-shrink-0 items-center gap-2 text-fg-subtle">
        {m.created_at && <span>{formatDate(m.created_at)}</span>}
        <span className="font-mono">#{m.id}</span>
      </span>
    </button>
  );

  return (
    <div className="rounded-xl border border-[var(--border)] p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-2 text-xs font-semibold text-fg">
        <span className="flex items-center gap-1.5"><Users className="h-3.5 w-3.5 text-accent" />{t("adm.users.referrals_title")}</span>
        <span className="font-normal text-fg-subtle">
          {[
            ...(data.referrer ? [t("adm.users.ref_has_inviter")] : []),
            t("adm.users.ref_invited_n", { n: data.counts.first }),
            ...(data.counts.second ? [t("adm.users.ref_second_n", { n: data.counts.second })] : []),
          ].join(" · ")}
          <ChevronDown className={`ml-1 inline h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {data.referrer && (
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-fg-subtle">{t("adm.users.ref_inviter")}</p>
              <Member m={data.referrer} />
            </div>
          )}
          {data.referrals.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-fg-subtle">{t("adm.users.ref_invited_title", { n: data.counts.first })}</p>
              <div className="space-y-1">{data.referrals.map(m => <Member key={m.id} m={m} />)}</div>
            </div>
          )}
          {data.second_level.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-fg-subtle">{t("adm.users.ref_second_title", { n: data.counts.second })}</p>
              <div className="space-y-1">{data.second_level.map(m => <Member key={m.id} m={m} />)}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Traffic by node ───────────────────────────────────────────────────────

function fmtBytes(n: number): string {
  if (n >= 1e12) return translate("adm.users.unit_tb", { v: (n / 1e12).toFixed(2) });
  if (n >= 1e9) return translate("adm.users.unit_gb", { v: (n / 1e9).toFixed(1) });
  if (n >= 1e6) return translate("adm.users.unit_mb", { v: (n / 1e6).toFixed(0) });
  if (n >= 1e3) return translate("adm.users.unit_kb", { v: (n / 1e3).toFixed(0) });
  return translate("adm.users.unit_b", { v: n });
}

function TrafficByNodeBlock({ userId }: { userId: number }) {
  const [data, setData] = useState<TrafficByNode | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState(30);
  const t = useT();

  useEffect(() => {
    setData(null);
    setFailed(false);
    usersAdminApi.trafficByNode(userId, days).then(setData).catch(() => setFailed(true));
  }, [userId, days]);

  // Прячем блок только если панель явно вернула "нет данных о нодах".
  if (data && (!data.available || data.nodes.length === 0)) return null;
  // …либо если запрос не удался вовсе. Раньше состояние ошибки было
  // неотличимо от загрузки (оба — data === null), и на бэкенде без этой ручки
  // блок навсегда застревал на «загрузка…».
  if (failed) return null;

  return (
    <div className="rounded-xl border border-[var(--border)] p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-2 text-xs font-semibold text-fg">
        <span className="flex items-center gap-1.5"><Gauge className="h-3.5 w-3.5 text-accent" />{t("adm.users.traffic_title")}</span>
        <span className="font-normal text-fg-subtle">
          {data ? t("adm.users.traffic_summary", { v: fmtBytes(data.total), n: data.days }) : t("adm.users.loading_lc")}
          <ChevronDown className={`ml-1 inline h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <div className="flex gap-1.5">
            {[30, 60, 90].map(d => (
              <button key={d} onClick={() => setDays(d)}
                className={`rounded-lg border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  days === d ? "border-accent bg-accent/10 text-accent" : "border-[var(--border)] text-fg-muted hover:text-fg"
                }`}>
                {t("adm.users.days_short", { n: d })}
              </button>
            ))}
          </div>
          {!data ? (
            <p className="py-2 text-center text-xs text-fg-subtle">{t("adm.users.loading_lc")}</p>
          ) : (
            <div className="space-y-1">
              {data.nodes.map((n, i) => {
                const pct = data.total > 0 ? Math.round((n.total / data.total) * 100) : 0;
                return (
                  <div key={i} className="rounded-lg border border-[var(--border)] px-3 py-2">
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate text-fg">
                        {n.country_code && <span className="mr-1 text-fg-muted">{n.country_code}</span>}{n.name}
                      </span>
                      <span className="flex-shrink-0 font-medium text-fg">{fmtBytes(n.total)}</span>
                    </div>
                    <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-bg-subtle">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Access grant (гранулярные роли) ───────────────────────────────────────

function AccessGrantBlock({ userId }: { userId: number }) {
  const [catalog, setCatalog] = useState<GrantCatalog | null>(null);
  const [grant, setGrant] = useState<UserGrant | null>(null);
  const [fullAccess, setFullAccess] = useState(false);
  const [canWrite, setCanWrite] = useState(true);
  const [secs, setSecs] = useState<Set<string>>(new Set());
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const t = useT();

  const load = useCallback(() => {
    Promise.all([grantsAdminApi.catalog(), grantsAdminApi.get(userId)])
      .then(([cat, g]) => {
        setCatalog(cat);
        setGrant(g);
        setFullAccess(g.full_access);
        setCanWrite(g.has_grant ? g.can_write : true);
        setSecs(new Set(g.sections));
        setExpires(g.expires_at ? g.expires_at.slice(0, 10) : "");
      })
      .catch(() => setMsg(t("adm.users.grant_load_err")));
  }, [userId, t]);
  useEffect(() => { load(); }, [load]);

  if (!catalog || !grant) return null;

  const applyPreset = (p: GrantPreset) => { setFullAccess(p.full_access); setSecs(new Set(p.sections)); };
  const toggleSec = (k: string) =>
    setSecs(prev => { const n = new Set(prev); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await grantsAdminApi.set(userId, {
        full_access: fullAccess,
        can_write: canWrite,
        sections: fullAccess ? [] : Array.from(secs),
        expires_at: expires ? new Date(expires + "T23:59:59").toISOString() : null,
      });
      setMsg(t("adm.users.saved")); load();
    } catch (e) { setMsg(e instanceof ApiError ? e.detail : t("adm.users.err_generic")); }
    finally { setBusy(false); }
  };
  const removeGrant = async () => {
    setBusy(true); setMsg(null);
    try { await grantsAdminApi.remove(userId); setMsg(t("adm.users.grant_removed")); load(); }
    catch (e) { setMsg(e instanceof ApiError ? e.detail : t("adm.users.err_generic")); }
    finally { setBusy(false); }
  };

  const eff = grant.effective;
  const effLabel = !eff.allowed
    ? t("adm.users.eff_none")
    : [
        eff.full_access ? t("adm.users.eff_full") : t("adm.users.eff_sections", { n: eff.sections.length }),
        ...(eff.can_write ? [] : [t("adm.users.eff_readonly")]),
      ].join(" · ");

  return (
    <div className="space-y-3 rounded-xl border border-[var(--border)] p-4">
      <div>
        <p className="text-xs font-semibold text-fg">{t("adm.users.grant_title")}</p>
        <p className="mt-0.5 text-[11px] text-fg-subtle">
          {t("adm.users.grant_hint")}
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {catalog.presets.map(p => (
          <button key={p.key} type="button" onClick={() => applyPreset(p)}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-fg-muted hover:bg-bg-subtle hover:text-fg">
            {p.label}
          </button>
        ))}
      </div>

      <label className="flex items-center gap-2 text-xs text-fg">
        <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={fullAccess}
          onChange={e => setFullAccess(e.target.checked)} />
        {t("adm.users.grant_full")}
      </label>
      <label className="flex items-center gap-2 text-xs text-fg">
        <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={!canWrite}
          onChange={e => setCanWrite(!e.target.checked)} />
        {t("adm.users.grant_readonly")}
      </label>

      {!fullAccess && (
        <div className="flex flex-wrap gap-1.5">
          {catalog.sections.map(s => (
            <button key={s.key} type="button" onClick={() => toggleSec(s.key)}
              className={`rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                secs.has(s.key)
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-[var(--border)] text-fg-muted hover:bg-bg-subtle hover:text-fg"
              }`}>
              {s.label}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 text-xs">
        <span className="text-fg-muted">{t("adm.users.sub_expires_label")}</span>
        <input type="date" value={expires} onChange={e => setExpires(e.target.value)}
          className="h-8 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
        {expires
          ? <button type="button" onClick={() => setExpires("")} className="text-fg-subtle hover:text-fg">{t("adm.users.grant_forever")}</button>
          : <span className="text-fg-subtle">{t("adm.users.grant_forever")}</span>}
      </div>

      {msg && <p className="text-xs text-fg-muted">{msg}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={save} disabled={busy}
          className="rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50">
          {busy ? t("adm.users.saving") : t("adm.users.btn_save_grant")}
        </button>
        {grant.has_grant && (
          <button type="button" onClick={removeGrant} disabled={busy}
            className="rounded-lg border border-danger/20 bg-danger/8 px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger/15 disabled:opacity-50">
            {t("adm.users.btn_remove_grant")}
          </button>
        )}
        <span className="ml-auto text-[11px] text-fg-subtle">{t("adm.users.grant_now", { v: effLabel })}</span>
      </div>
    </div>
  );
}

// ─── User Detail Modal ─────────────────────────────────────────────────────

function UserDetailModal({ userId, onClose, onUpdated, onOpenUser }: { userId: number; onClose: () => void; onUpdated: () => void; onOpenUser?: (id: number) => void }) {
  const [detail, setDetail] = useState<AdminUserDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [discountPersonal, setDiscountPersonal] = useState("");
  const [discountPurchase, setDiscountPurchase] = useState("");
  const t = useT();
  const { isOwner, isReadonlyAdmin, canSection } = useAuth();
  const { can: hasFeature } = useBranding();
  const { can, note } = useCapabilities();

  // Вкладка «Подписка» целиком живёт на разделе subscriptions: бэкенд, который
  // его не объявил, ответит на неё ошибкой. Раз вкладки может не быть — и
  // открываться первой она может не всегда.
  const canSub = canSection("subscriptions");
  const tabs = (["sub", "info", "tx"] as const).filter(k => k !== "sub" || canSub);
  const [tab, setTab] = useState<"info" | "sub" | "tx">(canSub ? "sub" : "info");

  const load = useCallback(() => {
    setLoading(true);
    usersAdminApi.get(userId)
      .then(d => { setDetail(d); setDiscountPersonal(String(d.user.personal_discount)); setDiscountPurchase(String(d.user.purchase_discount)); })
      .catch(e => setError(e instanceof ApiError ? e.detail : t("adm.users.err_generic")))
      .finally(() => setLoading(false));
  }, [userId, t]);

  useEffect(() => { load(); }, [load]);

  // Моб. фикс: жёстко блокируем скролл фона. На iOS body{overflow:hidden} НЕ
  // работает — нужен position:fixed с сохранением позиции. + Esc закрывает.
  useEffect(() => {
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = { position: body.style.position, top: body.style.top, width: body.style.width, overflow: body.style.overflow };
    body.style.position = "fixed";
    body.style.top = `-${scrollY}px`;
    body.style.width = "100%";
    body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => {
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      body.style.overflow = prev.overflow;
      window.scrollTo(0, scrollY);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const toggleBlock = async () => {
    if (!detail) return;
    const willBlock = !detail.user.is_blocked;
    if (willBlock && !confirm(t("adm.users.confirm_block"))) return;
    setSaving(true);
    try {
      await usersAdminApi.block(userId, willBlock);
      load(); onUpdated();
    } catch (e) { if (!note("users.block", e)) alert(e instanceof ApiError ? e.detail : t("adm.users.err_generic")); }
    finally { setSaving(false); }
  };

  const saveDiscount = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      await usersAdminApi.setDiscount(userId, Number(discountPersonal), Number(discountPurchase));
      load(); onUpdated();
    } catch (e) { if (!note("users.discount", e)) alert(e instanceof ApiError ? e.detail : t("adm.users.err_generic")); }
    finally { setSaving(false); }
  };

  const u = detail?.user;
  const roleMeta = u ? roleInfo(u.role) : null;

  // ВАЖНО (iOS-фикс): рендерим модалку ПОРТАЛОМ в document.body. Иначе она
  // остаётся внутри <main class="app-scroll">, у которого -webkit-overflow-scrolling:
  // touch на iOS Safari создаёт отдельный композиционный слой и ЗАПИРАЕТ в нём
  // position:fixed-потомков → z-50 модалки теряет силу против корневого топбара
  // (z-20), и шапка с крестиком «Закрыть» уезжает под топбар (см. скрин владельца).
  // Портал выносит модалку из этого слоя — крестик снова наверху и кликается.
  return createPortal(
    <div onClick={onClose} className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-3 pt-[max(1.5rem,env(safe-area-inset-top))] sm:p-4 sm:pt-8">
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-bg shadow-raised">
        {/* Header */}
        <div className="flex flex-shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10 text-accent text-sm font-semibold">
              {u?.name?.[0]?.toUpperCase() ?? "?"}
            </div>
            <div>
              <p className="text-sm font-semibold text-fg">{u?.name ?? t("adm.users.loading")}</p>
              {u?.username && <p className="text-xs text-fg-muted">@{u.username}</p>}
            </div>
          </div>
          <button onClick={onClose} aria-label={t("adm.users.close")} className="-m-1 rounded-lg p-2 text-fg-muted hover:text-fg"><X className="h-5 w-5" /></button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-10"><div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
        ) : error ? (
          <p className="p-5 text-sm text-danger">{error}</p>
        ) : u && detail ? (
          <>
            {/* Tabs */}
            <div className="flex border-b border-[var(--border)] px-5">
              {tabs.map(k => (
                <button key={k} onClick={() => setTab(k)}
                  className={`mr-4 border-b-2 pb-2.5 pt-3 text-xs font-medium transition-colors ${tab === k ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg"}`}>
                  {k === "sub" ? t("adm.users.tab_sub") : k === "info" ? t("adm.users.tab_info") : t("adm.users.tab_tx")}
                </button>
              ))}
            </div>

            <div className="p-5">
              {/* Subscription tab */}
              {tab === "sub" && <SubscriptionPanel userId={userId} points={detail?.user.points ?? 0} balance={detail?.user.cabinet_balance ?? 0} onUpdated={() => { load(); onUpdated(); }} />}

              {/* Info tab */}
              {tab === "info" && (
                <div className="space-y-5">
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    {[
                      ["ID", String(u.id)],
                      ["Telegram ID", u.telegram_id ? String(u.telegram_id) : "—"],
                      ["Email", u.email ?? "—"],
                      [t("adm.users.f_email_verified"), u.is_email_verified ? t("adm.users.yes") : t("adm.users.no")],
                      [t("adm.users.f_role"), roleMeta?.label ?? "—"],
                      [t("adm.users.f_lang"), u.language],
                      [t("adm.users.f_ref_code"), u.referral_code],
                      // Баллы — механика бота, а не кабинета. Там, где её нет,
                      // «0» читается как «человек ничего не накопил», хотя
                      // копить нечего в принципе.
                      ...(hasFeature("points") ? [[t("adm.users.f_points"), String(u.points)]] : []),
                      [t("adm.users.f_created"), u.created_at ? formatDate(u.created_at) : "—"],
                      [t("adm.users.f_trial_available"), u.is_trial_available ? t("adm.users.yes") : t("adm.users.no")],
                      [t("adm.users.f_last_login"), detail.logins?.last_login_at ? formatDate(detail.logins.last_login_at) : "—"],
                      [t("adm.users.f_logins_ips"), detail.logins ? `${detail.logins.total} / ${detail.logins.distinct_ips}` : "—"],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-lg border border-[var(--border)] p-3">
                        <p className="text-fg-subtle mb-0.5">{label}</p>
                        <p className="text-fg font-medium truncate">{value}</p>
                      </div>
                    ))}
                  </div>

                  {/* История входов */}
                  <LoginHistoryBlock userId={userId} />

                  {/* Реф-связи: кто пригласил + кого пригласил (клик открывает карточку) */}
                  <ReferralsBlock userId={userId} onOpenUser={onOpenUser} />

                  {/* Трафик по нодам (живьём из панели) */}
                  <TrafficByNodeBlock userId={userId} />

                  {/* Discounts. Карточка существует ради кнопки «Сохранить»:
                      менять нечем — показывать два поля незачем. */}
                  {!isReadonlyAdmin && can("users.discount") && (
                  <div className="rounded-xl border border-[var(--border)] p-4">
                    <p className="mb-3 text-xs font-semibold text-fg">{t("adm.users.discounts_title")}</p>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="mb-1 block text-xs text-fg-muted">{t("adm.users.discount_permanent")}</label>
                        <input type="number" min={0} max={100} value={discountPersonal} onChange={e => setDiscountPersonal(e.target.value)}
                          className="h-8 w-full rounded-lg border border-[var(--border)] bg-bg px-3 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
                        <p className="mt-1 text-[11px] text-fg-subtle">{t("adm.users.discount_permanent_hint")}</p>
                      </div>
                      <div>
                        <label className="mb-1 block text-xs text-fg-muted">{t("adm.users.discount_next")}</label>
                        <input type="number" min={0} max={100} value={discountPurchase} onChange={e => setDiscountPurchase(e.target.value)}
                          className="h-8 w-full rounded-lg border border-[var(--border)] bg-bg px-3 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
                        <p className="mt-1 text-[11px] text-fg-subtle">{t("adm.users.discount_next_hint")}</p>
                      </div>
                    </div>
                    <button onClick={saveDiscount} disabled={saving}
                      className="mt-3 rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50 transition-colors">
                      {saving ? t("adm.users.saving") : t("adm.users.btn_save_discounts")}
                    </button>
                  </div>
                  )}

                  {/* Доступ к админке (гранулярные роли) — только владелец, не для владельцев/системных */}
                  {isOwner && u.role != null && u.role < 5 && (
                    <AccessGrantBlock userId={userId} />
                  )}

                  {/* Block */}
                  {!isReadonlyAdmin && can("users.block") && (
                  <button onClick={toggleBlock} disabled={saving}
                    className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${u.is_blocked ? "border-success/20 bg-success/8 text-success hover:bg-success/15" : "border-danger/20 bg-danger/8 text-danger hover:bg-danger/15"}`}>
                    {u.is_blocked ? <><CheckCircle className="h-4 w-4" />{t("adm.users.btn_unblock")}</> : <><Ban className="h-4 w-4" />{t("adm.users.btn_block")}</>}
                  </button>
                  )}
                </div>
              )}

              {/* Transactions tab */}
              {tab === "tx" && (
                <div className="space-y-1.5">
                  {detail.transactions.length === 0 ? (
                    <p className="py-6 text-center text-sm text-fg-muted">{t("adm.users.tx_none")}</p>
                  ) : detail.transactions.map(tx => (
                    <div key={tx.payment_id} className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2.5 text-xs">
                      <div>
                        <p className="text-fg font-medium">{tx.gateway_type} · {tx.purchase_type}</p>
                        {tx.created_at && <p className="text-fg-subtle mt-0.5">{formatDate(tx.created_at)}</p>}
                      </div>
                      <span className={`font-medium ${tx.status === "COMPLETED" ? "text-success" : tx.status === "PENDING" ? "text-warning" : "text-fg-muted"}`}>{tx.status}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ─── Main Page ─────────────────────────────────────────────────────────────

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");      // "" = все, иначе число роли
  const [statusFilter, setStatusFilter] = useState("");  // "", "active", "blocked"
  const [sortBy, setSortBy] = useState("created_at");    // created_at | last_login | name
  const [sortOrder, setSortOrder] = useState("desc");    // asc | desc
  const [expiring, setExpiring] = useState("");          // "" = все, иначе N дней
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [bulkAction, setBulkAction] = useState("");
  const [bulkValue, setBulkValue] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);
  // Массовые задачи: какой диалог открыт и чьим получателям пишем («Написать получившим»).
  const [bulkDialog, setBulkDialog] = useState<"days" | "message" | null>(null);
  const [messageSource, setMessageSource] = useState<number | null>(null);
  const [jobsRefresh, setJobsRefresh] = useState(0);
  const t = useT();
  const { isReadonlyAdmin, fullAccess } = useAuth();
  const { can: hasFeature } = useBranding();
  const { can, note, disable } = useCapabilities();

  // Фильтры и сортировка — единственные органы управления, чью доступность
  // бэкенд объявляет не заранее, а прямо в ответ на запрос списка (501 «так не
  // умею»). Чтобы понять, ИЗ-ЗА ЧЕГО пришёл отказ, запоминаем, что админ
  // поменял последним: одним движением меняется ровно один фильтр.
  const pendingFilter = useRef<string | null>(null);

  // Возврат фильтра к тому виду, который бэкенд отдаёт. Без него список так и
  // остался бы пустым с красной плашкой, хотя данные прекрасно читаются.
  const resetFilter = useCallback((key: string) => {
    if (key === "users.filter.role") setRoleFilter("");
    else if (key === "users.filter.expiring") setExpiring("");
    else if (key === "users.sort") setSortBy("created_at");
    else if (key === "users.order") setSortOrder("desc");
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    usersAdminApi.list({
      limit: LIMIT, offset,
      search: search || undefined,
      role: roleFilter ? Number(roleFilter) : undefined,
      blocked: statusFilter === "blocked" ? true : statusFilter === "active" ? false : undefined,
      sort: sortBy, order: sortOrder,
      expiring: expiring ? Number(expiring) : undefined,
    })
      .then(r => { setUsers(r.items); setTotal(r.total); setError(null); pendingFilter.current = null; })
      .catch(e => {
        const key = pendingFilter.current;
        pendingFilter.current = null;
        // «Не умею» про только что выбранный фильтр — не ошибка экрана: убираем
        // сам фильтр и перезапрашиваем список без него.
        if (key && note(key, e)) { resetFilter(key); return; }
        setError(e instanceof ApiError ? e.detail : t("adm.users.err_generic"));
      })
      .finally(() => setLoading(false));
  }, [offset, search, roleFilter, statusFilter, sortBy, sortOrder, expiring, note, resetFilter, t]);

  useEffect(() => { load(); }, [load]);

  const handleSearch = (v: string) => { pendingFilter.current = null; setSearch(v); setOffset(0); };
  // `key` — какой именно фильтр трогают: по нему разбираем отказ бэкенда выше.
  const setFilter = (key: string | null, fn: () => void) => { pendingFilter.current = key; fn(); setOffset(0); };

  const [exporting, setExporting] = useState(false);
  const handleExport = async () => {
    setExporting(true);
    setError(null);
    try {
      await usersAdminApi.exportXlsx({
        search: search || undefined,
        role: roleFilter ? Number(roleFilter) : undefined,
        blocked: statusFilter === "blocked" ? true : statusFilter === "active" ? false : undefined,
        sort: sortBy, order: sortOrder,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : t("adm.users.export_failed");
      if (NO_EXPORT_ROUTE.test(msg)) { disable("users.export"); return; }
      setError(msg);
    } finally {
      setExporting(false);
    }
  };

  // Фильтры страницы в форме массовых задач — ровно то, что сейчас в списке.
  const bulkFilters = {
    search: search || undefined,
    role: roleFilter ? Number(roleFilter) : undefined,
    blocked: statusFilter === "blocked" ? true : statusFilter === "active" ? false : undefined,
    expiring: expiring ? Number(expiring) : undefined,
  };
  const closeBulkDialog = useCallback(() => { setBulkDialog(null); setMessageSource(null); }, []);
  const bulkStarted = useCallback(() => {
    setBulkDialog(null);
    setMessageSource(null);
    setBulkMsg(t("adm.users.bulk_started"));
    setJobsRefresh(n => n + 1);
  }, [t]);
  // «Не умею» от ручки массовой задачи — пункт исчезает, диалог закрывается.
  const bulkUnsupported = useCallback((key: string) => {
    disable(key);
    setBulkDialog(null);
    setMessageSource(null);
  }, [disable]);

  const BULK_LABELS: Record<string, string> = {
    points: t("adm.users.bulk_lbl_points"), discount: t("adm.users.bulk_lbl_discount"),
    block: t("adm.users.bulk_lbl_block"), unblock: t("adm.users.bulk_lbl_unblock"),
  };
  const applyBulk = async () => {
    if (!bulkAction) return;
    const v = Number(bulkValue) || 0;
    if (bulkAction === "points" && v === 0) { setBulkMsg(t("adm.users.bulk_points_zero")); return; }
    if (bulkAction === "discount" && (v < 0 || v > 100)) { setBulkMsg(t("adm.users.bulk_discount_range")); return; }
    if (!confirm(t("adm.users.bulk_confirm", { action: BULK_LABELS[bulkAction] ?? bulkAction, n: total }))) return;
    setBulkBusy(true); setBulkMsg(null);
    try {
      const r = await usersAdminApi.bulkAction({
        action: bulkAction as "points" | "discount" | "block" | "unblock",
        value: v,
        search: search || undefined,
        role: roleFilter ? Number(roleFilter) : undefined,
        blocked: statusFilter === "blocked" ? true : statusFilter === "active" ? false : undefined,
        expiring: expiring ? Number(expiring) : undefined,
      });
      setBulkMsg(t("adm.users.bulk_done", { applied: r.applied, matched: r.matched }));
      load();
    } catch (e) {
      if (!note("users.bulk", e)) setBulkMsg(e instanceof ApiError ? e.detail : t("adm.users.err_generic"));
    } finally {
      setBulkBusy(false);
    }
  };

  const totalPages = Math.ceil(total / LIMIT);
  const page = Math.floor(offset / LIMIT) + 1;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-fg">{t("adm.users.title")}</h1>
        <div className="flex items-center gap-3">
          <span className="hidden text-sm text-fg-muted sm:inline">{t("adm.users.total_n", { n: total })}</span>
          {/* Обновить список. Без неё оставалось только перезагружать страницу
              целиком — а это сбрасывает фильтры, поиск и позицию в списке. */}
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay disabled:opacity-50"
            title={t("adm.users.refresh_hint")}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            <span className="hidden sm:inline">{t("adm.users.btn_refresh")}</span>
          </button>
          {can("users.export") && (
          <button
            onClick={handleExport}
            disabled={exporting || total === 0}
            className="inline-flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay disabled:opacity-50"
            title={t("adm.users.export_hint")}
          >
            <Download className="h-4 w-4" />
            {exporting ? t("adm.users.export_busy") : t("adm.users.btn_export")}
          </button>
          )}
        </div>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" />
        <input
          value={search} onChange={e => handleSearch(e.target.value)}
          placeholder={t("adm.users.search_ph")}
          className="h-9 w-full rounded-lg border border-[var(--border)] bg-bg pl-9 pr-3 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>

      {/* Фильтры и сортировка */}
      <div className="flex flex-wrap items-center gap-2">
        {can("users.filter.role") && (
        <select
          value={roleFilter}
          onChange={e => setFilter("users.filter.role", () => setRoleFilter(e.target.value))}
          className="h-9 rounded-lg border border-[var(--border)] bg-bg px-2.5 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="">{t("adm.users.filter_role_all")}</option>
          <option value="1">{t("adm.users.role_user")}</option>
          <option value="2">{t("adm.users.role_admin_view")}</option>
          <option value="3">{t("adm.users.role_admin")}</option>
          <option value="5">{t("adm.users.role_owner")}</option>
        </select>
        )}
        {/* Статус (активен/заблокирован) отдельного ключа не имеет: это тот же
            список, и бэкенд, умеющий список, умеет и его. */}
        <select
          value={statusFilter}
          onChange={e => setFilter(null, () => setStatusFilter(e.target.value))}
          className="h-9 rounded-lg border border-[var(--border)] bg-bg px-2.5 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="">{t("adm.users.filter_status_all")}</option>
          <option value="active">{t("adm.users.filter_status_active")}</option>
          <option value="blocked">{t("adm.users.filter_status_blocked")}</option>
        </select>
        {can("users.filter.expiring") && (
        <select
          value={expiring}
          onChange={e => setFilter("users.filter.expiring", () => setExpiring(e.target.value))}
          title={t("adm.users.filter_expiring_hint")}
          className="h-9 rounded-lg border border-[var(--border)] bg-bg px-2.5 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="">{t("adm.users.filter_expiring_any")}</option>
          {[3, 7, 14, 30].map(d => (
            <option key={d} value={d}>{t("adm.users.filter_expiring_n", { n: d })}</option>
          ))}
        </select>
        )}
        {(can("users.sort") || can("users.order")) && (
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-fg-subtle">{t("adm.users.sort_label")}</span>
          {can("users.sort") && (
          <select
            value={sortBy}
            onChange={e => setFilter("users.sort", () => setSortBy(e.target.value))}
            className="h-9 rounded-lg border border-[var(--border)] bg-bg px-2.5 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="created_at">{t("adm.users.sort_created")}</option>
            <option value="last_login">{t("adm.users.f_last_login")}</option>
            <option value="name">{t("adm.users.sort_name")}</option>
          </select>
          )}
          {can("users.order") && (
          <button
            type="button"
            onClick={() => setFilter("users.order", () => setSortOrder(o => o === "asc" ? "desc" : "asc"))}
            title={sortOrder === "asc" ? t("adm.users.sort_asc") : t("adm.users.sort_desc")}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] bg-bg text-fg-muted hover:text-fg"
          >
            {sortOrder === "asc" ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
          )}
        </div>
        )}
      </div>

      {/* Массовые действия над текущей выборкой (только обычные пользователи) */}
      {!isReadonlyAdmin && can("users.bulk") && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2.5">
          <span className="text-xs font-medium text-fg-muted">{t("adm.users.bulk_label")}</span>
          <select
            value={bulkAction}
            aria-label={t("adm.users.bulk_aria")}
            onChange={e => {
              const value = e.target.value;
              setBulkMsg(null);
              // Дни и сообщение — не «значение + Применить», а диалог с предпросмотром:
              // селект сразу возвращается в исходное положение.
              if (value === "days" || value === "message") { setBulkAction(""); setBulkDialog(value); return; }
              setBulkAction(value);
            }}
            className="h-8 rounded-lg border border-[var(--border)] bg-bg px-2 text-xs text-fg focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="">{t("adm.users.bulk_choose")}</option>
            {/* Баллы и персональные скидки — механики бота: там, где их нет,
                пункт списка обещал бы несуществующее. */}
            {hasFeature("points") && <option value="points">{t("adm.users.bulk_opt_points")}</option>}
            <option value="discount">{t("adm.users.bulk_opt_discount")}</option>
            <option value="block">{t("adm.users.btn_block")}</option>
            <option value="unblock">{t("adm.users.btn_unblock")}</option>
            {/* Раздача дней и сообщение сотням людей — только полный доступ: модератор
                с разделом «Пользователи» блокирует одного, но не раздаёт дни всем. */}
            {fullAccess && hasFeature("bulk_days") && can("users.bulk.days") && (
              <option value="days">{t("adm.users.bulk_opt_days")}</option>
            )}
            {fullAccess && hasFeature("bulk_message") && can("users.bulk.message") && (
              <option value="message">{t("adm.users.bulk_opt_message")}</option>
            )}
          </select>
          {(bulkAction === "points" || bulkAction === "discount") && (
            <input
              type="number"
              value={bulkValue}
              onChange={e => setBulkValue(e.target.value)}
              placeholder={bulkAction === "discount" ? t("adm.users.bulk_ph_discount") : t("adm.users.bulk_ph_points")}
              className="h-8 w-28 rounded-lg border border-[var(--border)] bg-bg px-2 text-xs text-fg focus:outline-none focus:ring-1 focus:ring-accent"
            />
          )}
          <button
            onClick={applyBulk}
            disabled={!bulkAction || bulkBusy}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
          >
            {bulkBusy ? t("adm.users.bulk_busy") : t("adm.users.btn_apply")}
          </button>
          {bulkMsg && <span className="text-xs text-fg-subtle">{bulkMsg}</span>}
        </div>
      )}

      {/* Фоновые задачи видны и read-only (без кнопок): это журнал, а не пульт. */}
      {(hasFeature("bulk_days") || hasFeature("bulk_message")) && can("users.bulk.jobs") && (
        <BulkJobsPanel
          fullAccess={fullAccess}
          readonly={isReadonlyAdmin}
          refreshKey={jobsRefresh}
          onWriteRecipients={(jobId) => { setMessageSource(jobId); setBulkDialog("message"); }}
          onUnsupported={disable}
        />
      )}
      {bulkDialog === "days" && (
        <BulkDaysDialog filters={bulkFilters} onClose={closeBulkDialog} onStarted={bulkStarted} onUnsupported={bulkUnsupported} />
      )}
      {bulkDialog === "message" && (
        <BulkMessageDialog
          filters={messageSource == null ? bulkFilters : null}
          sourceJobId={messageSource}
          onClose={closeBulkDialog}
          onStarted={bulkStarted}
          onUnsupported={bulkUnsupported}
        />
      )}

      {error && <div className="flex items-center gap-2 rounded-lg bg-danger/8 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-16"><div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : (
        <>
          {/* Desktop: таблица. Моб.: карточная раскладка ниже (без гориз. скролла). */}
          <div className="hidden overflow-x-auto rounded-xl border border-[var(--border)] md:block">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] bg-bg-subtle">
                  {[t("adm.users.col_user"), "Email", t("adm.users.f_role"), t("adm.users.col_status"), "ID", t("adm.users.col_created"), t("adm.users.col_last_login")].map(h => (
                    <th key={h} className="px-4 py-2.5 text-left text-xs font-medium text-fg-muted">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map((u, i) => {
                  const rInfo = roleInfo(u.role, String(u.role));
                  // У read-only id скрыт сервером (null) → карточку не открыть.
                  const clickable = u.id != null;
                  return (
                    <tr
                      key={u.id ?? `row-${i}`}
                      onClick={clickable ? () => setSelectedId(u.id) : undefined}
                      className={`border-b border-[var(--border)] transition-colors ${clickable ? "cursor-pointer hover:bg-bg-subtle" : ""} ${i === users.length - 1 ? "border-0" : ""}`}
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-bg-raised border border-[var(--border)] text-xs font-medium text-fg-muted">
                            {u.name?.[0]?.toUpperCase() ?? "?"}
                          </div>
                          <div className="min-w-0">
                            <p className="truncate font-medium text-fg">{u.name}</p>
                            {u.username && <p className="text-xs text-fg-subtle">@{u.username}</p>}
                            {u.expire_at && <p className="text-xs text-warning">{t("adm.users.row_expires", { d: formatDate(u.expire_at) })}</p>}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-fg-muted">{u.email ?? "—"}</td>
                      <td className={`px-4 py-3 text-xs font-medium ${rInfo.cls}`}>{rInfo.label}</td>
                      <td className="px-4 py-3">
                        {u.is_blocked ? (
                          <span className="text-xs font-medium text-danger">{t("adm.users.status_blocked")}</span>
                        ) : (
                          <span className="text-xs font-medium text-success">{t("adm.users.status_active")}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-fg-muted font-mono">
                        <span title={t("adm.users.id_internal_hint")}>{u.id ?? "—"}</span>
                        {u.telegram_id ? (
                          <span className="block text-[11px] text-fg-subtle" title="Telegram ID">
                            tg {u.telegram_id}
                          </span>
                        ) : (
                          <span className="block text-[11px] text-fg-subtle">{t("adm.users.no_telegram")}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-fg-muted">{u.created_at ? formatDate(u.created_at) : "—"}</td>
                      <td className="px-4 py-3 text-xs text-fg-muted">{u.last_login_at ? formatDate(u.last_login_at) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile: карточки вместо таблицы (не влезает по ширине → был гориз. скролл) */}
          <div className="grid gap-2 md:hidden">
            {users.map((u, i) => {
              const rInfo = roleInfo(u.role, String(u.role));
              const clickable = u.id != null;
              return (
                <button
                  key={u.id ?? `card-${i}`}
                  type="button"
                  onClick={clickable ? () => setSelectedId(u.id) : undefined}
                  disabled={!clickable}
                  className="w-full rounded-xl border border-[var(--border)] bg-bg-subtle/40 p-3 text-left transition-colors enabled:hover:bg-bg-subtle disabled:cursor-default"
                >
                  <div className="flex items-start gap-2.5">
                    <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-bg-raised text-xs font-medium text-fg-muted">
                      {u.name?.[0]?.toUpperCase() ?? "?"}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate font-medium text-fg">{u.name}</p>
                        {u.is_blocked ? (
                          <span className="flex-shrink-0 text-xs font-medium text-danger">{t("adm.users.status_blocked")}</span>
                        ) : (
                          <span className="flex-shrink-0 text-xs font-medium text-success">{t("adm.users.status_active")}</span>
                        )}
                      </div>
                      {u.username && <p className="truncate text-xs text-fg-subtle">@{u.username}</p>}
                      {u.email && <p className="truncate text-xs text-fg-muted">{u.email}</p>}
                      {u.expire_at && <p className="text-xs text-warning">{t("adm.users.row_expires", { d: formatDate(u.expire_at) })}</p>}
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-fg-subtle">
                        <span className={`font-medium ${rInfo.cls}`}>{rInfo.label}</span>
                        <span className="font-mono">ID {u.id ?? "—"}</span>
                        <span className="font-mono">
                          {u.telegram_id ? `tg ${u.telegram_id}` : t("adm.users.no_telegram")}
                        </span>
                        {u.created_at && <span>{t("adm.users.row_registered", { d: formatDate(u.created_at) })}</span>}
                        {u.last_login_at && <span>{t("adm.users.row_login", { d: formatDate(u.last_login_at) })}</span>}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-sm">
              <button onClick={() => setOffset(Math.max(0, offset - LIMIT))} disabled={offset === 0}
                className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-fg-muted hover:text-fg disabled:opacity-40 transition-colors">
                <ChevronLeft className="h-3.5 w-3.5" /> {t("adm.users.btn_prev")}
              </button>
              <span className="text-xs text-fg-muted">{t("adm.users.page_of", { p: page, n: totalPages })}</span>
              <button onClick={() => setOffset(offset + LIMIT)} disabled={offset + LIMIT >= total}
                className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-fg-muted hover:text-fg disabled:opacity-40 transition-colors">
                {t("adm.users.btn_next")} <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </>
      )}

      {selectedId !== null && (
        <UserDetailModal userId={selectedId} onClose={() => setSelectedId(null)} onUpdated={load} onOpenUser={(id) => setSelectedId(id)} />
      )}
    </div>
  );
}
