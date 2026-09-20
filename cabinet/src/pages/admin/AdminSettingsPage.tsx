import { useEffect, useMemo, useState } from "react";
import { Save, AlertCircle, CheckCircle2, Bell, Lock, SlidersHorizontal } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { settingsAdminApi, topupAdminApi, morningSummaryAdminApi, trialDiscountAdminApi, reserveAdminApi, plansAdminApi, promoBannerAdminApi, winbackAdminApi, renewalDiscountAdminApi, digestAdminApi, digestEmailAdminApi, trafficAlertAdminApi, newDeviceAdminApi, loginAlertAdminApi, emailGateAdminApi, freezeAdminApi, type AdminSettings, type TopupAdminConfig, type TopupApplicability, type MorningSummaryConfig, type TrialDiscountConfig, type TrialDiscountDryRun, type ReserveConfig, type ReserveSquadCheck, type AdminSquad, type PromoBannerConfig, type WinbackConfig, type RenewalDiscountConfig, type DigestConfig, type DigestEmailStatus, type DigestEmailPreview, type DigestEmailDryRun, type DigestEmailOutcome, type TrafficAlertConfig, type NewDeviceConfig, type LoginAlertConfig, type FreezeConfig } from "@/api/admin";
import { ApiError } from "@/types/api";
import { useAuth } from "@/contexts/AuthContext";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";

// Крупный блок настроек: заголовок с иконкой + вложенные секции (карточки).
function Group({ title, icon: Icon, children }: { title: string; icon: LucideIcon; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Icon className="h-[18px] w-[18px] text-accent" />
        <h2 className="text-base font-bold text-fg md:text-lg">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Section({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        {desc && <p className="mt-0.5 text-xs text-fg-muted">{desc}</p>}
      </div>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

/** Just the switch control — used standalone or inside a row. */
function Switch({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg ${
        checked ? "bg-accent" : "bg-border"
      } ${disabled ? "cursor-not-allowed" : ""}`}
    >
      <span
        className={`inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
          checked ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

/** A full clickable row: label/sub on the left, switch pinned right inside a contained card.
 *  disabled — настройка у бэкенда есть, значение настоящее, но правится не отсюда;
 *  причина приходит в `sub` (см. `locked` в ответе адаптера). */
function Toggle({
  label,
  sub,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  sub?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={`flex w-full items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
        disabled
          ? "cursor-not-allowed border-border-subtle bg-bg opacity-60"
          : checked
            ? "border-accent/30 bg-accent/5 hover:bg-accent/10"
            : "border-border-subtle bg-bg hover:bg-bg-subtle"
      }`}
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-fg">{label}</p>
        {sub && <p className="mt-0.5 text-xs leading-snug text-fg-muted">{sub}</p>}
      </div>
      <Switch checked={checked} onChange={onChange} disabled={disabled} />
    </button>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  disabled,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-fg-muted">{label}</label>
      <input
        type={type}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent ${
          disabled ? "cursor-not-allowed opacity-60" : ""
        }`}
      />
      {hint && <p className="mt-1 text-xs leading-snug text-fg-muted">{hint}</p>}
    </div>
  );
}

// Человекочитаемые названия уведомлений — ключами перевода: экран двуязычный.
const NOTIFICATION_KEYS: Record<string, string> = {
  SUBSCRIPTION: "adm.settings.notif_subscription",
  BOT_LIFECYCLE: "adm.settings.notif_bot_lifecycle",
  TRIAL_ACTIVATED: "adm.settings.notif_trial_activated",
  USER_REGISTERED: "adm.settings.notif_user_registered",
  EXPIRES_IN_1_DAY: "adm.settings.notif_expires_1d",
  EXPIRES_IN_2_DAYS: "adm.settings.notif_expires_2d",
  EXPIRES_IN_3_DAYS: "adm.settings.notif_expires_3d",
  EXPIRED_1_DAY_AGO: "adm.settings.notif_expired_1d",
  REFERRAL_ATTACHED: "adm.settings.notif_referral_attached",
  REFERRAL_REWARD_RECEIVED: "adm.settings.notif_referral_reward",
  NODE_STATUS_CHANGED: "adm.settings.notif_node_status",
  NODE_TRAFFIC_REACHED: "adm.settings.notif_node_traffic",
  PROMOCODE_ACTIVATED: "adm.settings.notif_promocode",
  USER_DEVICES_UPDATED: "adm.settings.notif_devices_updated",
  USER_FIRST_CONNECTION: "adm.settings.notif_first_connection",
  USER_REVOKED_SUBSCRIPTION: "adm.settings.notif_revoked_sub",
};

// Незнакомое событие бэкенда переводить нечем — показываем его код словами, как и
// раньше: это техническое имя, а не текст интерфейса.
function prettyNotification(key: string): string {
  const tkey = NOTIFICATION_KEYS[key];
  if (tkey) return translate(tkey);
  return key
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

/** Что на подключённом бэкенде вообще применимо.
 *
 *  `supported` — чего у него нет вовсе: такой переключатель мы не рисуем. Показать
 *  его нельзя не из вежливости: сохранение экрана уходит одним запросом, и отказ
 *  по одному неприменимому полю отменяет заодно все соседние правки.
 *  `locked` — настройка есть и значение настоящее, но правится не отсюда (задана в
 *  .env бота, живёт отдельным списком): рисуем выключенной и подписываем причиной.
 *  Полей нет — бэкенд наш, экран работает как раньше. */
type Applicability = {
  supported?: Record<string, boolean>;
  locked?: Record<string, string>;
};

/** Карточка не загрузилась: беда это или «здесь такого не бывает».
 *
 *  501 от бэкенда означает ровно второе — кабинет стоит поверх чужого бота, и эта
 *  механика у него не переведена (или её у него нет вовсе). Тогда карточку не
 *  показываем совсем: «Не удалось загрузить» на месте функции, которой не
 *  существует, читается как поломка нашего кабинета, и владелец идёт её чинить.
 *  Любая другая беда (сеть, 500) — настоящая ошибка, и о ней надо сказать. */
const UNSUPPORTED = "__unsupported__";
const loadError = (e: unknown) =>
  e instanceof ApiError && e.status === 501 ? UNSUPPORTED : translate("adm.settings.load_failed");

export default function AdminSettingsPage() {
  const t = useT();
  const [settings, setSettings] = useState<(AdminSettings & Applicability) | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    settingsAdminApi
      .get()
      .then(setSettings)
      .catch((e) => setError(e instanceof ApiError ? e.detail : translate("adm.settings.err_generic")))
      .finally(() => setLoading(false));
  }, []);

  // Показывать элемент? Поля нет — да (наш бэкенд про `supported` не знает).
  const can = (key: string) => settings?.supported?.[key] !== false;
  // Причина «правится не отсюда»; есть — элемент рисуем выключенным.
  const why = (key: string) => settings?.locked?.[key];
  // Отправлять ли поле в сохранение: только то, что видно И правится.
  const editable = (key: string) => can(key) && !why(key);

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      // Кладём в тело ТОЛЬКО применимое. Раньше уходил весь экран целиком, и на
      // чужом бэкенде это заканчивалось отказом по первому же неприменимому полю
      // — вместе с ним терялись и настоящие правки в соседних блоках.
      const body: Record<string, unknown> = {};
      if (editable("access_mode") || editable("registration_allowed") || editable("payments_allowed")) {
        body.access = settings.access;
      }
      if (editable("registration_allowed")) body.registration_allowed = settings.access.registration_allowed;
      if (editable("payments_allowed")) body.payments_allowed = settings.access.payments_allowed;
      if (editable("rules_required")) body.rules_required = settings.requirements.rules_required;
      if (editable("channel_required")) body.channel_required = settings.requirements.channel_required;
      if (editable("channel_link")) body.channel_link = settings.requirements.channel_link;
      if (editable("rules_link")) body.rules_link = settings.requirements.rules_link;
      const backup: Record<string, unknown> = {};
      if (editable("backup_enabled")) backup.enabled = settings.backup.enabled;
      if (editable("backup_send_to_chat")) backup.send_to_chat = settings.backup.send_to_chat;
      if (editable("backup_interval_hours")) backup.interval_hours = settings.backup.interval_hours;
      if (editable("backup_max_files")) backup.max_files = settings.backup.max_files;
      if (Object.keys(backup).length) body.backup = backup;
      if (editable("trial_channel_guard")) body.trial_channel_guard = settings.extra.trial_channel_guard;
      if (editable("mini_app_reserve")) body.mini_app_reserve = settings.extra.mini_app_reserve;
      // Три сброса. Раньше уходил только «Сброс ссылки» и только чужому бэкенду:
      // наш их не принимал вовсе, и слать их значило бы делать вид, что тумблер
      // работает. Теперь принимает — шлём все три, а чужому по-прежнему только
      // то, что он сам объявил применимым.
      for (const key of ["device_single_reset", "device_all_reset", "link_reset"] as const) {
        if (editable(key)) body[key] = { enabled: settings.extra[key].enabled };
      }
      // Запертые переключатели уведомлений в тело не кладём — их значение всё
      // равно диктует .env бота, а отказ по ним отменил бы остальные.
      const notifications = Object.fromEntries(
        Object.entries(settings.notifications).filter(([key]) => !why(`notifications.${key}`)),
      );
      if (Object.keys(notifications).length) body.notifications = notifications;

      const updated = await settingsAdminApi.update(body);
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  const upd = (path: string[], value: unknown) => {
    setSettings((prev) => {
      if (!prev) return prev;
      const copy = JSON.parse(JSON.stringify(prev)) as AdminSettings;
      let cur: Record<string, unknown> = copy as unknown as Record<string, unknown>;
      for (let i = 0; i < path.length - 1; i++) {
        cur = cur[path[i]!] as Record<string, unknown>;
      }
      cur[path[path.length - 1]!] = value;
      return copy;
    });
  };

  const setAllNotifications = (value: boolean) => {
    setSettings((prev) => {
      if (!prev) return prev;
      const copy = JSON.parse(JSON.stringify(prev)) as AdminSettings & Applicability;
      for (const key of Object.keys(copy.notifications)) {
        // Запертые (.env бота) не трогаем: иначе «Включить все» переключил бы
        // на экране то, что на сохранении всё равно откатится к прежнему.
        if (copy.locked?.[`notifications.${key}`]) continue;
        copy.notifications[key] = value;
      }
      return copy;
    });
  };

  const notifStats = useMemo(() => {
    if (!settings) return { on: 0, total: 0 };
    const values = Object.values(settings.notifications);
    return { on: values.filter(Boolean).length, total: values.length };
  }, [settings]);

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  if (!settings) return null;

  // Текстовое поле, которое правится не отсюда, показываем только с содержимым:
  // пустая заблокированная строка — шум, а не информация.
  const showChannelLink = can("channel_link") && (!why("channel_link") || !!settings.requirements.channel_link);
  const showRulesLink = can("rules_link") && (!why("rules_link") || !!settings.requirements.rules_link);
  const backupKeys = ["backup_enabled", "backup_send_to_chat", "backup_interval_hours", "backup_max_files"];
  const showBackup = backupKeys.some(can);
  const extraKeys = ["trial_channel_guard", "mini_app_reserve", "device_single_reset", "device_all_reset", "link_reset"];
  const showExtra = extraKeys.some(can);
  const canSwitchAllNotifications = Object.keys(settings.notifications).some((key) => !why(`notifications.${key}`));
  // Есть ли на экране хоть что-то, что кнопка «Сохранить» реально изменит.
  // Если нет — кнопки быть не должно: «Сохранено!» без единой правки это враньё.
  const canSaveAnything =
    [...backupKeys, "trial_channel_guard", "mini_app_reserve", "access_mode", "registration_allowed",
      "payments_allowed", "rules_required", "channel_required", "channel_link", "rules_link"].some(editable) ||
    ["device_single_reset", "device_all_reset", "link_reset"].some((key) => can(key) && !why(key)) ||
    (can("notifications") && canSwitchAllNotifications);

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      {/* Sticky header */}
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-border-subtle bg-bg/80 px-5 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <h1 className="text-xl font-bold text-fg md:text-2xl">{t("adm.settings.title")}</h1>
        {canSaveAnything ? (
        <button
          onClick={save}
          disabled={saving}
          className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-50 ${
            saved ? "bg-success text-white" : "bg-accent text-accent-fg hover:bg-accent/90"
          }`}
        >
          {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? t("adm.settings.saved_bang") : saving ? t("adm.settings.saving") : t("adm.settings.save")}
        </button>
        ) : (
          <span className="max-w-[60%] text-right text-xs leading-snug text-fg-muted">
            {t("adm.settings.nothing_to_edit")}
          </span>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      <Group title={t("adm.settings.grp_access")} icon={Lock}>
      {/* Access */}
      {(can("access_mode") || can("registration_allowed") || can("payments_allowed")) && (
      <Section title={t("adm.settings.access_title")} desc={t("adm.settings.access_desc")}>
        {can("access_mode") && (
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.settings.access_mode")}</label>
          <select
            value={settings.access.mode}
            onChange={(e) => upd(["access", "mode"], e.target.value)}
            className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="PUBLIC">{t("adm.settings.mode_public")}</option>
            <option value="INVITED">{t("adm.settings.mode_invited")}</option>
            <option value="RESTRICTED">{t("adm.settings.mode_restricted")}</option>
          </select>
        </div>
        )}
        {(can("registration_allowed") || can("payments_allowed")) && (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {can("registration_allowed") && <Toggle label={t("adm.settings.registration_allowed")} sub={why("registration_allowed")} disabled={!!why("registration_allowed")} checked={settings.access.registration_allowed} onChange={(v) => upd(["access", "registration_allowed"], v)} />}
          {can("payments_allowed") && <Toggle label={t("adm.settings.payments_allowed")} sub={why("payments_allowed")} disabled={!!why("payments_allowed")} checked={settings.access.payments_allowed} onChange={(v) => upd(["access", "payments_allowed"], v)} />}
        </div>
        )}
      </Section>
      )}

      {/* Requirements */}
      <Section title={t("adm.settings.req_title")} desc={t("adm.settings.req_desc")}>
        {can("rules_required") && <Toggle label={t("adm.settings.req_rules")} sub={why("rules_required") ?? t("adm.settings.req_rules_sub")} disabled={!!why("rules_required")} checked={settings.requirements.rules_required} onChange={(v) => upd(["requirements", "rules_required"], v)} />}
        {can("channel_required") && <Toggle label={t("adm.settings.req_channel")} sub={why("channel_required") ?? t("adm.settings.req_channel_sub")} disabled={!!why("channel_required")} checked={settings.requirements.channel_required} onChange={(v) => upd(["requirements", "channel_required"], v)} />}
        {/* Поле, которое правится не отсюда, показываем только когда в нём что-то
            есть: пустая серая строка не рассказывает ни о чём. */}
        {(showChannelLink || showRulesLink) && (
        <div className="grid gap-4 pt-1 sm:grid-cols-2">
          {showChannelLink && <Field label={t("adm.settings.channel_link")} value={settings.requirements.channel_link} disabled={!!why("channel_link")} hint={why("channel_link")} onChange={(v) => upd(["requirements", "channel_link"], v)} />}
          {showRulesLink && <Field label={t("adm.settings.rules_link")} value={settings.requirements.rules_link} disabled={!!why("rules_link")} hint={why("rules_link")} onChange={(v) => upd(["requirements", "rules_link"], v)} />}
        </div>
        )}
        <EmailGateToggle />
      </Section>

      <LoginAlertCard />

      </Group>

      <Group title={t("adm.settings.grp_system")} icon={SlidersHorizontal}>
      {/* Backup */}
      {showBackup && (
      <Section title={t("adm.settings.backup_title")}>
        {(can("backup_enabled") || can("backup_send_to_chat")) && (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {can("backup_enabled") && <Toggle label={t("adm.settings.backup_enabled")} sub={why("backup_enabled")} disabled={!!why("backup_enabled")} checked={settings.backup.enabled} onChange={(v) => upd(["backup", "enabled"], v)} />}
          {can("backup_send_to_chat") && <Toggle label={t("adm.settings.backup_send_chat")} sub={why("backup_send_to_chat")} disabled={!!why("backup_send_to_chat")} checked={settings.backup.send_to_chat} onChange={(v) => upd(["backup", "send_to_chat"], v)} />}
        </div>
        )}
        {(can("backup_interval_hours") || can("backup_max_files")) && (
        <div className="grid grid-cols-2 gap-4 pt-1">
          {can("backup_interval_hours") && <Field label={t("adm.settings.backup_interval")} type="number" value={String(settings.backup.interval_hours)} disabled={!!why("backup_interval_hours")} hint={why("backup_interval_hours")} onChange={(v) => upd(["backup", "interval_hours"], Number(v))} />}
          {can("backup_max_files") && <Field label={t("adm.settings.backup_max_files")} type="number" value={String(settings.backup.max_files)} disabled={!!why("backup_max_files")} hint={why("backup_max_files")} onChange={(v) => upd(["backup", "max_files"], Number(v))} />}
        </div>
        )}
      </Section>
      )}

      {/* Extra */}
      {showExtra && (
      <Section title={t("adm.settings.extra_title")}>
        {can("trial_channel_guard") && <Toggle label={t("adm.settings.extra_trial_guard")} sub={why("trial_channel_guard") ?? t("adm.settings.extra_trial_guard_sub")} disabled={!!why("trial_channel_guard")} checked={settings.extra.trial_channel_guard} onChange={(v) => upd(["extra", "trial_channel_guard"], v)} />}
        <div className="grid gap-2.5 sm:grid-cols-2">
          {can("mini_app_reserve") && <Toggle label={t("adm.settings.extra_mini_app")} sub={why("mini_app_reserve")} disabled={!!why("mini_app_reserve")} checked={settings.extra.mini_app_reserve} onChange={(v) => upd(["extra", "mini_app_reserve"], v)} />}
          {can("device_single_reset") && <Toggle label={t("adm.settings.extra_device_single")} sub={why("device_single_reset")} disabled={!!why("device_single_reset")} checked={settings.extra.device_single_reset.enabled} onChange={(v) => upd(["extra", "device_single_reset", "enabled"], v)} />}
          {can("device_all_reset") && <Toggle label={t("adm.settings.extra_device_all")} sub={why("device_all_reset")} disabled={!!why("device_all_reset")} checked={settings.extra.device_all_reset.enabled} onChange={(v) => upd(["extra", "device_all_reset", "enabled"], v)} />}
          {can("link_reset") && <Toggle label={t("adm.settings.extra_link_reset")} sub={why("link_reset")} disabled={!!why("link_reset")} checked={settings.extra.link_reset.enabled} onChange={(v) => upd(["extra", "link_reset", "enabled"], v)} />}
        </div>
      </Section>
      )}

      {/* Notifications */}
      {can("notifications") && (
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-fg-muted" />
            <h3 className="text-sm font-semibold text-fg">{t("adm.settings.notif_title")}</h3>
            <span className="rounded-full bg-bg px-2 py-0.5 text-xs font-medium text-fg-muted">
              {notifStats.on} / {notifStats.total}
            </span>
          </div>
          {/* Кнопки «все» прячем, когда менять нечего: все переключатели заперты
              настройками бота, и нажатие ничего бы не изменило. */}
          {canSwitchAllNotifications && (
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setAllNotifications(true)}
              className="rounded-lg border border-border-subtle bg-bg px-3 py-1.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
            >
              {t("adm.settings.notif_all_on")}
            </button>
            <button
              type="button"
              onClick={() => setAllNotifications(false)}
              className="rounded-lg border border-border-subtle bg-bg px-3 py-1.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
            >
              {t("adm.settings.notif_all_off")}
            </button>
          </div>
          )}
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2">
          {Object.entries(settings.notifications).map(([key, enabled]) => (
            <Toggle key={key} label={prettyNotification(key)} sub={why(`notifications.${key}`)} disabled={!!why(`notifications.${key}`)} checked={enabled} onChange={(v) => upd(["notifications", key], v)} />
          ))}
        </div>
      </section>
      )}

      </Group>
    </div>
  );
}

export function PromoBannerCard() {
  const t = useT();
  const [cfg, setCfg] = useState<PromoBannerConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    promoBannerAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<PromoBannerConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await promoBannerAdminApi.update(cfg);
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.promo_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title={t("adm.settings.promo_title")} desc={t("adm.settings.promo_desc")}>
      <Toggle label={t("adm.settings.promo_enabled")} sub={t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div>
        <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_f_title")}</label>
        <input type="text" value={cfg.title} onChange={(e) => patch({ title: e.target.value })} className={inputCls} placeholder={t("adm.settings.promo_title_ph")} />
      </div>
      <div>
        <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_f_text")}</label>
        <textarea value={cfg.text} onChange={(e) => patch({ text: e.target.value })} className={inputCls} rows={2} placeholder={t("adm.settings.promo_text_ph")} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_cta_text")}</label>
          <input type="text" value={cfg.cta_text} onChange={(e) => patch({ cta_text: e.target.value })} className={inputCls} placeholder={t("adm.settings.promo_cta_text_ph")} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_cta_url")}</label>
          <input type="text" value={cfg.cta_url} onChange={(e) => patch({ cta_url: e.target.value })} className={inputCls} placeholder="/billing" />
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_color")}</label>
          <select value={cfg.color} onChange={(e) => patch({ color: e.target.value as PromoBannerConfig["color"] })} className={inputCls}>
            <option value="accent">{t("adm.settings.promo_color_accent")}</option>
            <option value="red">{t("adm.settings.promo_color_red")}</option>
            <option value="green">{t("adm.settings.promo_color_green")}</option>
            <option value="amber">{t("adm.settings.promo_color_amber")}</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_audience")}</label>
          <select value={cfg.audience} onChange={(e) => patch({ audience: e.target.value as PromoBannerConfig["audience"] })} className={inputCls}>
            <option value="all">{t("adm.settings.promo_aud_all")}</option>
            <option value="no_sub">{t("adm.settings.promo_aud_no_sub")}</option>
            <option value="has_sub">{t("adm.settings.promo_aud_has_sub")}</option>
            <option value="trial">{t("adm.settings.promo_aud_trial")}</option>
            <option value="expiring">{t("adm.settings.promo_aud_expiring")}</option>
          </select>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_starts_at")}</label>
          <input type="datetime-local" value={cfg.starts_at ? cfg.starts_at.slice(0, 16) : ""} onChange={(e) => patch({ starts_at: e.target.value ? new Date(e.target.value).toISOString() : "" })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.promo_ends_at")}</label>
          <input type="datetime-local" value={cfg.ends_at ? cfg.ends_at.slice(0, 16) : ""} onChange={(e) => patch({ ends_at: e.target.value ? new Date(e.target.value).toISOString() : "" })} className={inputCls} />
        </div>
      </div>
      <Toggle label={t("adm.settings.promo_dismissible")} sub={t("adm.settings.promo_dismissible_sub")} checked={cfg.dismissible} onChange={(v) => patch({ dismissible: v })} />
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

export function ReserveCard() {
  const t = useT();
  const [cfg, setCfg] = useState<ReserveConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Сквады панели — чтобы выбирать из списка, а не вклеивать UUID руками. Опечатка в
  // UUID означала бы «резерв включён, а сервера нет», причём молча.
  const [squads, setSquads] = useState<AdminSquad[] | null>(null);
  // Вердикт панели по выбранному скваду. Без него владелец узнавал бы, что сквад
  // пустой или спрятан от хостов, только из жалобы клиента.
  const [squadCheck, setSquadCheck] = useState<ReserveSquadCheck | null>(null);

  useEffect(() => {
    reserveAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
    // Панель может быть недоступна (или бэкенд — чужой): тогда просто оставим поле
    // ручного ввода, вместо того чтобы ломать карточку.
    plansAdminApi.squads().then((r) => setSquads(r.internal)).catch(() => setSquads([]));
  }, []);

  // Проверяем сквад при открытии страницы и при каждой смене выбора: ошибку настройки
  // надо показывать здесь, а не через неделю в жалобе. Ручки нет у адаптера поверх
  // чужого бота — тогда просто ничего не показываем.
  const squadUuid = cfg?.squad_uuid ?? "";
  useEffect(() => {
    if (!squadUuid) {
      setSquadCheck(null);
      return;
    }
    let stale = false;
    reserveAdminApi
      .squadCheck(squadUuid)
      .then((r) => !stale && setSquadCheck(r))
      .catch(() => !stale && setSquadCheck(null));
    return () => {
      stale = true;
    };
  }, [squadUuid]);

  const patch = (p: Partial<ReserveConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  // Тот же договор применимости, что и в «Настройках»: поля нет в ответе — элемент
  // показываем (наш бэкенд про `supported` не знает); `false` — не показываем вовсе;
  // `locked` — показываем выключенным и подписываем причиной.
  const can = (key: string) => cfg?.supported?.[key] !== false;
  const why = (key: string) => cfg?.locked?.[key];
  const editable = (key: string) => can(key) && !why(key);
  // Бэкенд с режимами (адаптер поверх чужого бота): у него вместо тумблера четыре
  // режима, срок в ЧАСАХ и два сквада. Признак — наличие самого поля, а не догадки.
  const graceMode = cfg?.mode !== undefined;
  const hasHours = cfg?.window_hours !== undefined;

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      // Кладём в тело только то, что видно И правится. На нашем бэкенде карт
      // применимости нет, а новых полей нет в ответе — уходят ровно прежние
      // четыре поля, как и до появления пульта к чужому боту.
      const body: Partial<ReserveConfig> = {};
      if (editable("enabled")) body.enabled = cfg.enabled;
      if (editable("reserve_gb")) {
        // Наш бэкенд держит резерв в 1…100 ГБ и сам обрежет лишнее; у чужого
        // бота свои границы, и молча ужимать его 200 ГБ до сотни нельзя —
        // отдаём число как есть, а причину отказа он скажет сам.
        const gb = Math.trunc(Number(cfg.reserve_gb) || 0);
        body.reserve_gb = graceMode ? Math.max(0, gb) : Math.min(100, Math.max(1, gb || 1));
      }
      if (editable("window_days")) body.window_days = Math.min(60, Math.max(1, Number(cfg.window_days) || 1));
      if (editable("squad_uuid")) body.squad_uuid = (cfg.squad_uuid || "").trim();
      if (graceMode && editable("mode")) body.mode = cfg.mode;
      // Часы НЕ пересчитываем в дни и обратно: тихое округление съедает остаток
      // срока (на паузе это уже стоило людям подаренных дней).
      if (hasHours && editable("window_hours")) body.window_hours = Math.max(1, Math.trunc(Number(cfg.window_hours) || 0));
      if (cfg.squad_uuid_limited !== undefined && editable("squad_uuid_limited")) {
        body.squad_uuid_limited = (cfg.squad_uuid_limited || "").trim();
      }
      if (cfg.trial_enabled !== undefined && editable("trial_enabled")) body.trial_enabled = cfg.trial_enabled;
      if (cfg.daily_enabled !== undefined && editable("daily_enabled")) body.daily_enabled = cfg.daily_enabled;
      if (cfg.free_enabled !== undefined && editable("free_enabled")) body.free_enabled = cfg.free_enabled;

      const updated = await reserveAdminApi.update(body);
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.reserve_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
  const modes = cfg.modes ?? [];
  const modeDesc = modes.find((m) => m.value === cfg.mode)?.desc;
  // У обоих бэкендов сквад обязателен, но по разным причинам: у чужого бота без него
  // не запускается их grace, у нас — резерв потерял бы смысл (человек остался бы на
  // обычных серверах, то есть с полным доступом бесплатно).
  const squadHint =
    why("squad_uuid") ??
    (graceMode
      ? t("adm.settings.reserve_squad_hint_grace")
      : t("adm.settings.reserve_squad_hint"));

  return (
    <Section
      title={t("adm.settings.reserve_title")}
      desc={
        graceMode
          ? t("adm.settings.reserve_desc_grace")
          : t("adm.settings.reserve_desc")
      }
    >
      {can("enabled") && (
        <Toggle
          label={t("adm.settings.reserve_enable")}
          sub={why("enabled") ?? t("adm.settings.reserve_enable_sub")}
          checked={cfg.enabled}
          onChange={(v) => patch({ enabled: v })}
          disabled={!!why("enabled")}
        />
      )}
      {graceMode && can("mode") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.reserve_mode")}</label>
          <select
            value={cfg.mode}
            disabled={!!why("mode")}
            onChange={(e) => patch({ mode: e.target.value })}
            className={`${inputCls} ${why("mode") ? "cursor-not-allowed opacity-60" : ""}`}
          >
            {modes.length
              ? modes.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))
              : <option value={cfg.mode}>{cfg.mode}</option>}
          </select>
          {modeDesc && <p className="mt-1 text-xs leading-snug text-fg-muted">{modeDesc}</p>}
          {why("mode") && <p className="mt-1 text-xs leading-snug text-fg-muted">{why("mode")}</p>}
          {cfg.mode_note && <p className="mt-1 text-xs leading-snug text-warning">{cfg.mode_note}</p>}
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {can("reserve_gb") && (
          <div>
            <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.reserve_gb")}</label>
            <input type="number" min={1} max={graceMode ? undefined : 100} disabled={!!why("reserve_gb")} value={String(cfg.reserve_gb)} onChange={(e) => patch({ reserve_gb: Number(e.target.value) })} className={`${inputCls} ${why("reserve_gb") ? "cursor-not-allowed opacity-60" : ""}`} />
            {why("reserve_gb") && <p className="mt-1 text-xs leading-snug text-fg-muted">{why("reserve_gb")}</p>}
          </div>
        )}
        {can("window_days") && (
          <div>
            <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.reserve_window_days")}</label>
            <input type="number" min={1} max={60} disabled={!!why("window_days")} value={String(cfg.window_days)} onChange={(e) => patch({ window_days: Number(e.target.value) })} className={`${inputCls} ${why("window_days") ? "cursor-not-allowed opacity-60" : ""}`} />
            {why("window_days") && <p className="mt-1 text-xs leading-snug text-fg-muted">{why("window_days")}</p>}
          </div>
        )}
        {hasHours && can("window_hours") && (
          <div>
            {/* Единицу подписываем явно и не пересчитываем: столько часов и уйдёт в бота. */}
            <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.reserve_window_hours")}</label>
            <input type="number" min={1} disabled={!!why("window_hours")} value={String(cfg.window_hours)} onChange={(e) => patch({ window_hours: Number(e.target.value) })} className={`${inputCls} ${why("window_hours") ? "cursor-not-allowed opacity-60" : ""}`} />
            <p className="mt-1 text-xs leading-snug text-fg-muted">
              {why("window_hours") ??
                t("adm.settings.reserve_hours_hint", {
                  h: cfg.window_hours ?? 0,
                  d: Math.round(((cfg.window_hours ?? 0) / 24) * 10) / 10,
                })}
            </p>
          </div>
        )}
      </div>
      {can("squad_uuid") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">
            {graceMode ? t("adm.settings.reserve_squad_grace") : t("adm.settings.reserve_squad")}
          </label>
          {squads && squads.length > 0 ? (
            <select
              disabled={!!why("squad_uuid")}
              value={cfg.squad_uuid}
              onChange={(e) => patch({ squad_uuid: e.target.value })}
              className={`${inputCls} ${why("squad_uuid") ? "cursor-not-allowed opacity-60" : ""}`}
            >
              <option value="">{t("adm.settings.reserve_squad_none")}</option>
              {/* Сквад из конфига может быть удалён из панели — показываем его отдельной
                  строкой, иначе select молча сбросил бы значение на «не выбран». */}
              {!squads.some((sq) => sq.uuid === cfg.squad_uuid) && cfg.squad_uuid && (
                <option value={cfg.squad_uuid}>{t("adm.settings.reserve_squad_missing", { uuid: cfg.squad_uuid })}</option>
              )}
              {squads.map((sq) => (
                <option key={sq.uuid} value={sq.uuid}>{sq.name}</option>
              ))}
            </select>
          ) : (
            <input type="text" disabled={!!why("squad_uuid")} value={cfg.squad_uuid} onChange={(e) => patch({ squad_uuid: e.target.value })} placeholder={t("adm.settings.reserve_squad_ph")} className={`${inputCls} ${why("squad_uuid") ? "cursor-not-allowed opacity-60" : ""}`} />
          )}
          {squadHint && <p className="mt-1 text-xs leading-snug text-fg-muted">{squadHint}</p>}
          {squadCheck?.checked && (
            <p className={`mt-1 text-xs leading-snug ${squadCheck.ok ? "text-fg-muted" : "text-warning"}`}>
              {squadCheck.ok
                ? t("adm.settings.reserve_check_ok", { n: squadCheck.hosts })
                : t("adm.settings.reserve_check_bad", { problems: squadCheck.problems.join("; ") })}
            </p>
          )}
        </div>
      )}
      {cfg.squad_uuid_limited !== undefined && can("squad_uuid_limited") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.reserve_squad_limited")}</label>
          <input type="text" disabled={!!why("squad_uuid_limited")} value={cfg.squad_uuid_limited} onChange={(e) => patch({ squad_uuid_limited: e.target.value })} placeholder={t("adm.settings.reserve_squad_ph")} className={`${inputCls} ${why("squad_uuid_limited") ? "cursor-not-allowed opacity-60" : ""}`} />
          <p className="mt-1 text-xs leading-snug text-fg-muted">
            {why("squad_uuid_limited") ?? t("adm.settings.reserve_squad_limited_hint")}
          </p>
        </div>
      )}
      {cfg.trial_enabled !== undefined && (
        <div className="space-y-2.5">
          <p className="text-xs text-fg-muted">{t("adm.settings.reserve_who")}</p>
          {can("trial_enabled") && (
            <Toggle label={t("adm.settings.reserve_who_trial")} sub={why("trial_enabled")} checked={!!cfg.trial_enabled} onChange={(v) => patch({ trial_enabled: v })} disabled={!!why("trial_enabled")} />
          )}
          {cfg.daily_enabled !== undefined && can("daily_enabled") && (
            <Toggle label={t("adm.settings.reserve_who_daily")} sub={why("daily_enabled")} checked={!!cfg.daily_enabled} onChange={(v) => patch({ daily_enabled: v })} disabled={!!why("daily_enabled")} />
          )}
          {cfg.free_enabled !== undefined && can("free_enabled") && (
            <Toggle label={t("adm.settings.reserve_who_free")} sub={why("free_enabled")} checked={!!cfg.free_enabled} onChange={(v) => patch({ free_enabled: v })} disabled={!!why("free_enabled")} />
          )}
        </div>
      )}
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

function EmailGateToggle() {
  const t = useT();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    emailGateAdminApi.get().then((c) => setEnabled(c.enabled)).catch(() => setEnabled(null));
  }, []);

  if (enabled === null) return null;

  const toggle = async (v: boolean) => {
    setBusy(true);
    try {
      const c = await emailGateAdminApi.update({ enabled: v });
      setEnabled(c.enabled);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Toggle
      label={t("adm.settings.email_gate")}
      sub={busy ? t("adm.settings.saving") : t("adm.settings.email_gate_sub")}
      checked={enabled}
      onChange={toggle}
    />
  );
}

export function FreezeCard() {
  const t = useT();
  const [cfg, setCfg] = useState<FreezeConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    freezeAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<FreezeConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await freezeAdminApi.update({
        enabled: cfg.enabled,
        max_days: Math.min(365, Math.max(1, Number(cfg.max_days) || 30)),
      });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.freeze_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title={t("adm.settings.freeze_title")} desc={t("adm.settings.freeze_desc")}>
      <Toggle label={t("adm.settings.freeze_enable")} sub={t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="sm:max-w-xs">
        <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.freeze_max_days")}</label>
        <input type="number" min={1} max={365} value={String(cfg.max_days)} onChange={(e) => patch({ max_days: Number(e.target.value) })} className={inputCls} />
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

export function NewDeviceCard() {
  const t = useT();
  const [cfg, setCfg] = useState<NewDeviceConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    newDeviceAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const save = async (enabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await newDeviceAdminApi.update({ enabled });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.newdev_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  return (
    <Section title={t("adm.settings.newdev_title")} desc={t("adm.settings.newdev_desc")}>
      <Toggle label={t("adm.settings.notify_enable")} sub={saving ? t("adm.settings.saving") : saved ? t("adm.settings.saved") : t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => save(v)} />
      {error && <span className="text-xs text-danger">{error}</span>}
    </Section>
  );
}

export function LoginAlertCard() {
  const t = useT();
  const [cfg, setCfg] = useState<LoginAlertConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loginAlertAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const save = async (enabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await loginAlertAdminApi.update({ enabled });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.login_title")}>{error ?? t("adm.settings.err_generic")}</Section>;

  return (
    <Section title={t("adm.settings.login_title")} desc={t("adm.settings.login_desc")}>
      <Toggle label={t("adm.settings.login_enable")} sub={saving ? t("adm.settings.saving") : saved ? t("adm.settings.saved") : t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => save(v)} />
      {error && <span className="text-xs text-danger">{error}</span>}
    </Section>
  );
}

export function TrafficAlertCard() {
  const t = useT();
  const [cfg, setCfg] = useState<TrafficAlertConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    trafficAlertAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<TrafficAlertConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await trafficAlertAdminApi.update({
        enabled: cfg.enabled,
        threshold_percent: Math.min(99, Math.max(50, Number(cfg.threshold_percent) || 80)),
      });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.traffic_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title={t("adm.settings.traffic_title")} desc={t("adm.settings.traffic_desc")}>
      <Toggle label={t("adm.settings.notify_enable")} sub={t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="sm:max-w-xs">
        <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.traffic_threshold")}</label>
        <input type="number" min={50} max={99} value={String(cfg.threshold_percent)} onChange={(e) => patch({ threshold_percent: Number(e.target.value) })} className={inputCls} />
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

export function DigestCard() {
  const t = useT();
  const [cfg, setCfg] = useState<DigestConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    digestAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<DigestConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await digestAdminApi.update({
        enabled: cfg.enabled,
        day_of_month: Math.min(28, Math.max(1, Number(cfg.day_of_month) || 1)),
        hour: Math.min(23, Math.max(0, Number(cfg.hour) || 0)),
      });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.digest_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title={t("adm.settings.digest_title")} desc={t("adm.settings.digest_desc")}>
      <Toggle label={t("adm.settings.digest_enable")} sub={t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.digest_day")}</label>
          <input type="number" min={1} max={28} value={String(cfg.day_of_month)} onChange={(e) => patch({ day_of_month: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.digest_hour")}</label>
          <input type="number" min={0} max={23} value={String(cfg.hour)} onChange={(e) => patch({ hour: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

// Исходы холостого прогона — словами, которыми их прочтёт владелец (ключи перевода).
const DIGEST_EMAIL_OUTCOMES: Record<DigestEmailOutcome, string> = {
  would_send: "adm.settings.dm_out_would_send",
  no_traffic: "adm.settings.dm_out_no_traffic",
  usage_error: "adm.settings.dm_out_usage_error",
  already_this_month: "adm.settings.dm_out_already",
};

/** Сводка письмом — тем, у кого нет ни Telegram, ни push.
 *
 *  Отдельная карточка, а не поля в «Месячном дайджесте»: у кабинета поверх чужого
 *  бота дайджест есть, а писем нет. Там ручка отвечает 501 (у голого бэкенда без
 *  неё — 404), и карточки просто нет — «Не удалось загрузить» на месте функции,
 *  которой не бывает, читалось бы как поломка.
 *
 *  Сохраняем только изменённые поля: включение проверяется на бэкенде (409 с
 *  причиной), и лишнее поле в теле не должно тянуть за собой чужой отказ. */
export function DigestEmailCard() {
  const t = useT();
  const [st, setSt] = useState<DigestEmailStatus | null>(null);
  const [hidden, setHidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [from, setFrom] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<DigestEmailPreview | null>(null);
  const [previewLang, setPreviewLang] = useState<"ru" | "en">("ru");
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [dry, setDry] = useState<DigestEmailDryRun | null>(null);
  const [checking, setChecking] = useState(false);
  const [dryErr, setDryErr] = useState<string | null>(null);
  const [testTo, setTestTo] = useState("");
  const [testing, setTesting] = useState(false);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const apply = (s: DigestEmailStatus) => {
    setSt(s);
    setEnabled(s.email_enabled);
    setFrom(s.email_from);
  };

  useEffect(() => {
    digestEmailAdminApi
      .get()
      .then(apply)
      .catch((e) => {
        if (e instanceof ApiError && (e.status === 404 || e.status === 501)) setHidden(true);
        else setLoadErr(translate("adm.settings.load_failed"));
      })
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    if (!st) return;
    const body: { email_enabled?: boolean; email_from?: string } = {};
    if (from.trim() !== st.email_from) body.email_from = from.trim();
    if (enabled !== st.email_enabled) body.email_enabled = enabled;
    setError(null);
    if (Object.keys(body).length === 0) {
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      return;
    }
    setSaving(true);
    try {
      apply(await digestEmailAdminApi.update(body));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      // 409 — включить нельзя, причина в detail дословно. Введённое не сбрасываем:
      // владелец поправит адрес и нажмёт ещё раз.
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  const showPreview = async (lang: "ru" | "en") => {
    setPreviewLang(lang);
    setPreviewErr(null);
    try {
      setPreview(await digestEmailAdminApi.preview(lang));
    } catch (e) {
      setPreview(null);
      setPreviewErr(e instanceof ApiError ? e.detail : t("adm.settings.dm_preview_failed"));
    }
  };

  const check = async () => {
    setChecking(true);
    setDryErr(null);
    try {
      setDry(await digestEmailAdminApi.dryRun());
    } catch (e) {
      setDry(null);
      setDryErr(e instanceof ApiError ? e.detail : t("adm.settings.check_failed"));
    } finally {
      setChecking(false);
    }
  };

  const sendTest = async () => {
    const to = testTo.trim();
    if (!to) return;
    setTesting(true);
    setTestMsg(null);
    try {
      const r = await digestEmailAdminApi.test(to);
      setTestMsg({
        ok: true,
        text: t("adm.settings.dm_test_sent", { to: r.to, from: r.from }),
      });
    } catch (e) {
      setTestMsg({ ok: false, text: e instanceof ApiError ? e.detail : t("adm.settings.dm_test_failed") });
    } finally {
      setTesting(false);
    }
  };

  if (loading || hidden) return null;
  if (!st) return <Section title={t("adm.settings.dm_title")}>{loadErr ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
  const ghostBtn = "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg-subtle disabled:opacity-50";
  const last = st.last;

  return (
    <Section
      title={t("adm.settings.dm_title")}
      desc={t("adm.settings.dm_desc")}
    >
      {!st.digest_enabled && (
        <p className="rounded-xl border border-border-subtle bg-bg px-4 py-3 text-xs text-warning">
          {t("adm.settings.dm_digest_off")}
        </p>
      )}
      {st.blockers.length > 0 && (
        <ul className="space-y-1 rounded-xl border border-border-subtle bg-bg px-4 py-3 text-xs leading-snug text-warning">
          {st.blockers.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      )}
      <Field
        label={st.needs_separate_sender ? t("adm.settings.dm_from_brevo") : t("adm.settings.dm_from")}
        value={from}
        onChange={setFrom}
        type="email"
        hint={t("adm.settings.dm_from_hint")}
      />
      {st.effective_from && (
        <p className="text-xs text-fg-muted">{t("adm.settings.dm_effective_from", { email: st.effective_from })}</p>
      )}
      <Toggle
        label={t("adm.settings.dm_send_enable")}
        sub={t("adm.settings.dm_send_enable_sub")}
        checked={enabled}
        onChange={setEnabled}
      />
      <p className="text-xs leading-snug text-fg-muted">
        {t("adm.settings.dm_audience", { n: st.audience, out: st.opted_out })}
      </p>
      {last && (
        <p className="text-xs leading-snug text-fg-muted">
          {t("adm.settings.dm_last", {
            month: last.month,
            sent: last.sent,
            failed: last.failed,
            no_traffic: last.no_traffic,
            usage_error: last.usage_error,
            over_limit: last.over_limit,
            provider_blocked: last.provider_blocked,
          })}
          {last.sending > 0 && ` ${t("adm.settings.dm_last_sending", { n: last.sending })}`}
        </p>
      )}
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>

      <div className="space-y-3 rounded-xl border border-border-subtle bg-bg px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => showPreview(previewLang)} className={ghostBtn}>
            {t("adm.settings.dm_preview_btn")}
          </button>
          {preview && (
            <select
              value={previewLang}
              onChange={(e) => showPreview(e.target.value as "ru" | "en")}
              className="rounded-xl border border-border-subtle bg-bg px-2 py-2 text-sm text-fg"
              aria-label={t("adm.settings.dm_lang_aria")}
            >
              <option value="ru">{t("adm.settings.dm_lang_ru")}</option>
              <option value="en">{t("adm.settings.dm_lang_en")}</option>
            </select>
          )}
          <button onClick={check} disabled={checking} className={ghostBtn}>
            {checking ? "…" : t("adm.settings.dm_check_btn")}
          </button>
        </div>
        {previewErr && <p className="text-xs text-danger">{previewErr}</p>}
        {preview && (
          <div className="space-y-2">
            <p className="text-xs text-fg-muted">{t("adm.settings.dm_subject")} <span className="text-fg">{preview.subject}</span></p>
            {/* sandbox без разрешений: вёрстка письма не исполняет скриптов и не
                трогает страницу админки. Ссылка отписки в примере — заглушка. */}
            <iframe
              title={t("adm.settings.dm_preview_title")}
              sandbox=""
              srcDoc={preview.html}
              className="h-[520px] w-full rounded-xl border border-border-subtle bg-white"
            />
          </div>
        )}
        {dryErr && <p className="text-xs text-danger">{dryErr}</p>}
        {dry && (
          <div className="space-y-2 text-xs text-fg-muted">
            <p className="text-fg">
              {t("adm.settings.dm_dry_summary", { examined: dry.examined, n: dry.would_send })}
            </p>
            {dry.truncated && <p>{t("adm.settings.dm_dry_truncated", { n: dry.examined, total: dry.audience })}</p>}
            {dry.items.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-fg-muted">
                      <th className="py-1 pr-3 font-medium">{t("adm.settings.dm_col_id")}</th>
                      <th className="py-1 pr-3 font-medium">{t("adm.settings.dm_col_gb")}</th>
                      <th className="py-1 pr-3 font-medium">{t("adm.settings.dm_col_favorite")}</th>
                      <th className="py-1 pr-3 font-medium">{t("adm.settings.dm_col_outcome")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dry.items.map((item, i) => (
                      <tr key={item.user_id ?? `row-${i}`} className="border-t border-border-subtle text-fg">
                        <td className="py-1 pr-3">{item.user_id ?? "—"}</td>
                        <td className="py-1 pr-3">{item.gb ?? "—"}</td>
                        <td className="py-1 pr-3">{item.favorite ?? "—"}</td>
                        <td className="py-1 pr-3">{DIGEST_EMAIL_OUTCOMES[item.outcome] ? t(DIGEST_EMAIL_OUTCOMES[item.outcome]) : item.outcome}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1">
            <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.dm_test_label")}</label>
            <input
              type="email"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              className={inputCls}
              placeholder="you@example.com"
            />
          </div>
          <button onClick={sendTest} disabled={testing || !testTo.trim()} className={ghostBtn}>
            {testing ? "…" : t("adm.settings.dm_test_btn")}
          </button>
        </div>
        {testMsg && <p className={`text-xs ${testMsg.ok ? "text-success" : "text-danger"}`}>{testMsg.text}</p>}
      </div>
    </Section>
  );
}

export function WinbackCard() {
  const t = useT();
  const [cfg, setCfg] = useState<WinbackConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    winbackAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<WinbackConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await winbackAdminApi.update({
        enabled: cfg.enabled,
        percent: Math.min(100, Math.max(1, Number(cfg.percent) || 1)),
        days_after: Math.min(90, Math.max(1, Number(cfg.days_after) || 1)),
        lifetime_hours: Math.min(1440, Math.max(1, Number(cfg.lifetime_hours) || 1)),
      });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.wb_title")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title={t("adm.settings.wb_title")} desc={t("adm.settings.wb_desc")}>
      <Toggle label={t("adm.settings.wb_enable")} sub={t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.f_percent")}</label>
          <input type="number" min={1} max={100} value={String(cfg.percent)} onChange={(e) => patch({ percent: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.wb_days_after")}</label>
          <input type="number" min={1} max={90} value={String(cfg.days_after)} onChange={(e) => patch({ days_after: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.f_promo_lifetime")}</label>
          <input type="number" min={1} max={1440} value={String(cfg.lifetime_hours)} onChange={(e) => patch({ lifetime_hours: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

/** Кому скидка до окончания подписки не выдаётся — те же правила, что в бэкенде
 *  (services/overlay_renewal_discount.py, decide). Держим рядом с полями, чтобы
 *  владелец видел цену решения до того, как включит. */
const RENEWAL_DISCOUNT_EXCLUSIONS = "adm.settings.rd_exclusions";

export function RenewalDiscountCard() {
  const t = useT();
  const [cfg, setCfg] = useState<RenewalDiscountConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    renewalDiscountAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<RenewalDiscountConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const minDays = cfg.min_days_before ?? 4;
      // Пределы повторяют бэкенд; он всё равно зажмёт сам, но так поле не прыгает
      // после сохранения на значение, которого админ не вводил.
      const updated = await renewalDiscountAdminApi.update({
        enabled: cfg.enabled,
        percent: Math.min(90, Math.max(1, Number(cfg.percent) || 1)),
        days_before: Math.min(Math.max(minDays, 30), Math.max(minDays, Number(cfg.days_before) || minDays)),
        lifetime_hours: Math.min(720, Math.max(24, Number(cfg.lifetime_hours) || 24)),
        cooldown_days: Math.min(365, Math.max(0, Number(cfg.cooldown_days) || 0)),
        skip_early_renewers: cfg.skip_early_renewers,
      });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.rd_title")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const minDays = cfg.min_days_before ?? 4;

  return (
    <Section
      title={t("adm.settings.rd_title")}
      desc={t("adm.settings.rd_desc")}
    >
      {cfg.note && (
        <p className="whitespace-pre-line rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-xs leading-relaxed text-fg">
          {cfg.note}
        </p>
      )}
      <Toggle
        label={t("adm.settings.enable")}
        sub={t("adm.settings.rd_enable_sub")}
        checked={cfg.enabled}
        onChange={(v) => patch({ enabled: v })}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("adm.settings.f_percent")} type="number" value={String(cfg.percent)} onChange={(v) => patch({ percent: Number(v) })} />
        <Field
          label={t("adm.settings.rd_days_before")}
          type="number"
          value={String(cfg.days_before)}
          onChange={(v) => patch({ days_before: Number(v) })}
          hint={t("adm.settings.rd_days_before_hint", { n: minDays })}
        />
        <Field
          label={t("adm.settings.rd_lifetime")}
          type="number"
          value={String(cfg.lifetime_hours)}
          onChange={(v) => patch({ lifetime_hours: Number(v) })}
          hint={t("adm.settings.rd_lifetime_hint")}
        />
        <Field
          label={t("adm.settings.rd_cooldown")}
          type="number"
          value={String(cfg.cooldown_days)}
          onChange={(v) => patch({ cooldown_days: Number(v) })}
          hint={t("adm.settings.rd_cooldown_hint")}
        />
      </div>
      <Toggle
        label={t("adm.settings.rd_skip_early")}
        sub={t("adm.settings.rd_skip_early_sub")}
        checked={cfg.skip_early_renewers}
        onChange={(v) => patch({ skip_early_renewers: v })}
      />
      <p className="text-xs leading-relaxed text-fg-muted">{t(RENEWAL_DISCOUNT_EXCLUSIONS)}</p>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

export function TrialDiscountCard() {
  const t = useT();
  const [cfg, setCfg] = useState<TrialDiscountConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Холостой прогон: «кого зацепит рассылка, если её включить». Единственный
  // способ проверить такую рассылку, не раздав живым людям настоящие скидки, —
  // до этого он был доступен только curl'ом. Есть не у всех бэкендов
  // (can_dry_run), у нашего собственного его нет вовсе, и кнопки тогда нет.
  const [dry, setDry] = useState<TrialDiscountDryRun | null>(null);
  const [checking, setChecking] = useState(false);
  const [dryError, setDryError] = useState<string | null>(null);

  useEffect(() => {
    trialDiscountAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const check = async () => {
    setChecking(true);
    setDryError(null);
    try {
      setDry(await trialDiscountAdminApi.dryRun());
    } catch (e) {
      setDry(null);
      setDryError(e instanceof ApiError ? e.detail : t("adm.settings.check_failed"));
    } finally {
      setChecking(false);
    }
  };

  const patch = (p: Partial<TrialDiscountConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await trialDiscountAdminApi.update({
        enabled: cfg.enabled,
        percent: Math.min(100, Math.max(1, Number(cfg.percent) || 1)),
        days_before: Math.min(30, Math.max(1, Number(cfg.days_before) || 1)),
        lifetime_hours: Math.min(720, Math.max(1, Number(cfg.lifetime_hours) || 1)),
      });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.td_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title={t("adm.settings.td_title")} desc={t("adm.settings.td_desc")}>
      {/* Бэкенд рассказал, что у него это устроено иначе (промокод вместо
          молчаливой выдачи) — печатаем дословно: описание выше написано про наш. */}
      {/* Дословно, с переносами: бэкенд кладёт сюда не только описание механизма,
          но и свои предупреждения («включено, но не работает», невручённые
          промокоды) отдельными абзацами. */}
      {cfg.note && (
        <p className="whitespace-pre-line rounded-xl border border-border-subtle bg-bg px-4 py-3 text-xs leading-relaxed text-fg-muted">
          {cfg.note}
        </p>
      )}
      <Toggle label={t("adm.settings.enable")} sub={t("adm.settings.off_by_default")} checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.f_percent")}</label>
          <input type="number" min={1} max={100} value={String(cfg.percent)} onChange={(e) => patch({ percent: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.td_days_before")}</label>
          <input type="number" min={1} max={30} value={String(cfg.days_before)} onChange={(e) => patch({ days_before: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.f_promo_lifetime")}</label>
          <input type="number" min={1} max={720} value={String(cfg.lifetime_hours)} onChange={(e) => patch({ lifetime_hours: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      {cfg.can_dry_run && (
        <div className="space-y-3 rounded-xl border border-border-subtle bg-bg px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-fg-muted">
              {t("adm.settings.td_dry_note")}
            </p>
            <button
              onClick={check}
              disabled={checking}
              className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg-subtle disabled:opacity-50"
            >
              {checking ? "…" : t("adm.settings.check_btn")}
            </button>
          </div>
          {dryError && <p className="text-xs text-danger">{dryError}</p>}
          {dry && (
            <div className="space-y-2 text-xs text-fg-muted">
              <p className="text-fg">
                {t(
                  dry.truncated
                    ? "adm.settings.td_dry_summary_truncated"
                    : "adm.settings.td_dry_summary",
                  { examined: dry.examined, n: dry.previews.filter((p) => p.would_send).length },
                )}
              </p>
              {dry.skipped && Object.keys(dry.skipped).length > 0 && (
                <ul className="space-y-0.5">
                  {Object.entries(dry.skipped).map(([reason, count]) => (
                    <li key={reason}>
                      {reason} — {count}
                    </li>
                  ))}
                </ul>
              )}
              {!!dry.has_discount && <p>{t("adm.settings.td_has_discount", { n: dry.has_discount })}</p>}
              {dry.discount_check && <p className="text-warning">{dry.discount_check}</p>}
              {dry.lifetime_note && <p>{dry.lifetime_note}</p>}
              {dry.orphan_note && (
                <p className="text-danger">
                  {dry.orphan_note}
                  {dry.orphan_codes?.length ? ` (${dry.orphan_codes.map((c) => c.code).join(", ")})` : ""}
                </p>
              )}
              {dry.previews.find((p) => p.would_send)?.text && (
                <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-bg-subtle p-3 text-[11px] leading-relaxed text-fg">
                  {dry.previews
                    .find((p) => p.would_send)!
                    .text!.replace(/<[^>]+>/g, "")}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

export function MorningSummaryCard() {
  const t = useT();
  const [cfg, setCfg] = useState<MorningSummaryConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    morningSummaryAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<MorningSummaryConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await morningSummaryAdminApi.update({
        enabled: cfg.enabled,
        hour: Math.min(23, Math.max(0, Number(cfg.hour) || 0)),
        expiring_days: Math.max(1, Number(cfg.expiring_days) || 1),
      });
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.ms_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title={t("adm.settings.ms_title")} desc={t("adm.settings.ms_desc")}>
      <Toggle label={t("adm.settings.ms_enable")} sub={t("adm.settings.ms_enable_sub")} checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.ms_hour")}</label>
          <input type="number" min={0} max={23} value={String(cfg.hour)} onChange={(e) => patch({ hour: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.ms_expiring_days")}</label>
          <input type="number" min={1} value={String(cfg.expiring_days)} onChange={(e) => patch({ expiring_days: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
        </button>
      </div>
    </Section>
  );
}

export function TopupSettingsCard({ applicability }: { applicability?: TopupApplicability } = {}) {
  const t = useT();
  // На чужом бэкенде («Бедолага») условия пополнения СОБИРАЮТСЯ из настроек
  // каждого платёжного метода, и общего места, куда их записать, нет. Цифры при
  // этом настоящие и полезные — поэтому показываем их, но без кнопки, за которой
  // отказ. Признак приходит от бэкенда (whoami.readonly_pages), у нашего
  // собственного этого поля нет вовсе — значит, всё как раньше.
  const { isPageReadonly } = useAuth();
  const readonly = isPageReadonly("/admin/topup");
  // Разбор по ПОЛЯМ, а не по странице целиком: у бэкенда, который правит условия
  // пополнения по каждому способу отдельно (карточка ниже), сама страница
  // редактируемая, а вот эти пять сводных значений — нет. Карт нет — бэкенд наш,
  // всё показывается и правится как раньше.
  const can = (key: string) => applicability?.supported?.[key] !== false;
  const why = (key: string) => applicability?.locked?.[key];
  const editable = (key: string) => can(key) && !why(key);
  const anyEditable = ["enabled", "bonus_percent", "min_amount", "max_amount", "presets"].some(editable);
  const [cfg, setCfg] = useState<TopupAdminConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    topupAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<TopupAdminConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const presets = String(cfg.presets.join(","))
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((n) => Number.isFinite(n) && n >= 1);
      // В тело кладём ТОЛЬКО применимое: сохранение уходит одним запросом, и
      // отказ по одному неприменимому полю отменил бы правки в соседних.
      const body: Partial<TopupAdminConfig> = {};
      if (editable("enabled")) body.enabled = cfg.enabled;
      if (editable("bonus_percent")) body.bonus_percent = Number(cfg.bonus_percent) || 0;
      if (editable("min_amount")) body.min_amount = Number(cfg.min_amount) || 1;
      if (editable("max_amount")) body.max_amount = Number(cfg.max_amount) || 1;
      if (editable("presets")) body.presets = presets;
      const updated = await topupAdminApi.update(body);
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.settings.err_save"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title={t("adm.settings.topup_title_short")}>{error ?? t("adm.settings.err_generic")}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";
  // Причина «правится не отсюда» — прямо под полем: без неё выключенный ввод
  // читается как поломка.
  const lockNote = (key: string) =>
    why(key) ? <p className="mt-1 text-[11px] leading-snug text-fg-muted">{why(key)}</p> : null;
  // Число колонок считаем по видимым полям: с тремя фиксированными колонками
  // спрятанный «Бонус» оставлял бы дыру.
  const numberFields = ["bonus_percent", "min_amount", "max_amount"].filter(can).length;

  return (
    <Section
      title={t("adm.settings.topup_title")}
      desc={
        can("bonus_percent")
          ? t("adm.settings.topup_desc_bonus")
          : t("adm.settings.topup_desc")
      }
    >
      {can("enabled") && (
        <Toggle
          label={t("adm.settings.topup_enable")}
          sub={why("enabled") ?? t("adm.settings.topup_enable_sub")}
          checked={cfg.enabled}
          disabled={!!why("enabled")}
          onChange={(v) => patch({ enabled: v })}
        />
      )}
      {numberFields > 0 && (
        <div className={`grid gap-3 ${numberFields >= 3 ? "sm:grid-cols-3" : numberFields === 2 ? "sm:grid-cols-2" : ""}`}>
          {can("bonus_percent") && (
            <div>
              <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.topup_bonus")}</label>
              <input type="number" min={0} max={100} disabled={!!why("bonus_percent")} value={String(cfg.bonus_percent)} onChange={(e) => patch({ bonus_percent: Number(e.target.value) })} className={inputCls} />
              {lockNote("bonus_percent")}
            </div>
          )}
          {can("min_amount") && (
            <div>
              <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.topup_min")}</label>
              <input type="number" min={1} disabled={!!why("min_amount")} value={String(cfg.min_amount)} onChange={(e) => patch({ min_amount: Number(e.target.value) })} className={inputCls} />
              {lockNote("min_amount")}
            </div>
          )}
          {can("max_amount") && (
            <div>
              <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.topup_max")}</label>
              <input type="number" min={1} disabled={!!why("max_amount")} value={String(cfg.max_amount)} onChange={(e) => patch({ max_amount: Number(e.target.value) })} className={inputCls} />
              {lockNote("max_amount")}
            </div>
          )}
        </div>
      )}
      {can("presets") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{t("adm.settings.topup_presets")}</label>
          <input type="text" disabled={!!why("presets")} value={cfg.presets.join(", ")} onChange={(e) => patch({ presets: e.target.value.split(",").map((s) => Number(s.trim())).filter((n) => !Number.isNaN(n)) })} className={inputCls} />
          {lockNote("presets")}
        </div>
      )}
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">{t("adm.settings.saved")}</span>}
          {readonly && (
            <span className="text-fg-muted">
              {t("adm.settings.topup_readonly")}
            </span>
          )}
        </span>
        {/* Кнопки нет, когда править нечем: у бэкенда, где каждое поле помечено
            «правится не отсюда», «Сохранить» вело бы в отказ. Карт нет — бэкенд
            наш, кнопка на месте. */}
        {!readonly && anyEditable && (
          <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
            <Save className="h-4 w-4" /> {saving ? "…" : t("adm.settings.save")}
          </button>
        )}
      </div>
    </Section>
  );
}
