import { useEffect, useMemo, useState } from "react";
import { Save, AlertCircle, CheckCircle2, Bell, Lock, SlidersHorizontal } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { settingsAdminApi, topupAdminApi, morningSummaryAdminApi, trialDiscountAdminApi, reserveAdminApi, promoBannerAdminApi, winbackAdminApi, digestAdminApi, trafficAlertAdminApi, newDeviceAdminApi, loginAlertAdminApi, emailGateAdminApi, freezeAdminApi, type AdminSettings, type TopupAdminConfig, type TopupApplicability, type MorningSummaryConfig, type TrialDiscountConfig, type ReserveConfig, type PromoBannerConfig, type WinbackConfig, type DigestConfig, type TrafficAlertConfig, type NewDeviceConfig, type LoginAlertConfig, type FreezeConfig } from "@/api/admin";
import { ApiError } from "@/types/api";
import { useAuth } from "@/contexts/AuthContext";

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

// Человекочитаемые названия уведомлений
const NOTIFICATION_LABELS: Record<string, string> = {
  SUBSCRIPTION: "Подписка",
  BOT_LIFECYCLE: "Запуск/остановка бота",
  TRIAL_ACTIVATED: "Активирован пробный период",
  USER_REGISTERED: "Новая регистрация",
  EXPIRES_IN_1_DAY: "Истекает через 1 день",
  EXPIRES_IN_2_DAYS: "Истекает через 2 дня",
  EXPIRES_IN_3_DAYS: "Истекает через 3 дня",
  EXPIRED_1_DAY_AGO: "Истекла 1 день назад",
  REFERRAL_ATTACHED: "Привязан реферал",
  REFERRAL_REWARD_RECEIVED: "Получена реферальная награда",
  NODE_STATUS_CHANGED: "Изменился статус ноды",
  NODE_TRAFFIC_REACHED: "Достигнут лимит трафика ноды",
  PROMOCODE_ACTIVATED: "Активирован промокод",
  USER_DEVICES_UPDATED: "Обновлены устройства пользователя",
  USER_FIRST_CONNECTION: "Первое подключение пользователя",
  USER_REVOKED_SUBSCRIPTION: "Пользователь отозвал подписку",
};

function prettyNotification(key: string): string {
  return (
    NOTIFICATION_LABELS[key] ??
    key
      .toLowerCase()
      .replace(/_/g, " ")
      .replace(/^\w/, (c) => c.toUpperCase())
  );
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
  e instanceof ApiError && e.status === 501 ? UNSUPPORTED : "Не удалось загрузить";

export default function AdminSettingsPage() {
  const [settings, setSettings] = useState<(AdminSettings & Applicability) | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    settingsAdminApi
      .get()
      .then(setSettings)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка"))
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
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
        <h1 className="text-xl font-bold text-fg md:text-2xl">Настройки</h1>
        {canSaveAnything ? (
        <button
          onClick={save}
          disabled={saving}
          className={`flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-50 ${
            saved ? "bg-success text-white" : "bg-accent text-accent-fg hover:bg-accent/90"
          }`}
        >
          {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? "Сохранено!" : saving ? "Сохранение…" : "Сохранить"}
        </button>
        ) : (
          <span className="max-w-[60%] text-right text-xs leading-snug text-fg-muted">
            Менять отсюда нечего: настройки этого бота заданы в его файле окружения (.env)
          </span>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      <Group title="Доступ и регистрация" icon={Lock}>
      {/* Access */}
      {(can("access_mode") || can("registration_allowed") || can("payments_allowed")) && (
      <Section title="Доступ" desc="Кто и как может пользоваться сервисом">
        {can("access_mode") && (
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-muted">Режим доступа</label>
          <select
            value={settings.access.mode}
            onChange={(e) => upd(["access", "mode"], e.target.value)}
            className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="PUBLIC">PUBLIC — открытый</option>
            <option value="INVITED">INVITED — только по приглашению</option>
            <option value="RESTRICTED">RESTRICTED — всё заблокировано</option>
          </select>
        </div>
        )}
        {(can("registration_allowed") || can("payments_allowed")) && (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {can("registration_allowed") && <Toggle label="Разрешить регистрацию" sub={why("registration_allowed")} disabled={!!why("registration_allowed")} checked={settings.access.registration_allowed} onChange={(v) => upd(["access", "registration_allowed"], v)} />}
          {can("payments_allowed") && <Toggle label="Разрешить оплату" sub={why("payments_allowed")} disabled={!!why("payments_allowed")} checked={settings.access.payments_allowed} onChange={(v) => upd(["access", "payments_allowed"], v)} />}
        </div>
        )}
      </Section>
      )}

      {/* Requirements */}
      <Section title="Требования" desc="Условия для пользователей при регистрации">
        {can("rules_required") && <Toggle label="Принять правила" sub={why("rules_required") ?? "Пользователь должен принять правила при регистрации"} disabled={!!why("rules_required")} checked={settings.requirements.rules_required} onChange={(v) => upd(["requirements", "rules_required"], v)} />}
        {can("channel_required") && <Toggle label="Обязательный канал" sub={why("channel_required") ?? "Пользователь должен подписаться на канал"} disabled={!!why("channel_required")} checked={settings.requirements.channel_required} onChange={(v) => upd(["requirements", "channel_required"], v)} />}
        {/* Поле, которое правится не отсюда, показываем только когда в нём что-то
            есть: пустая серая строка не рассказывает ни о чём. */}
        {(showChannelLink || showRulesLink) && (
        <div className="grid gap-4 pt-1 sm:grid-cols-2">
          {showChannelLink && <Field label="Ссылка на канал" value={settings.requirements.channel_link} disabled={!!why("channel_link")} hint={why("channel_link")} onChange={(v) => upd(["requirements", "channel_link"], v)} />}
          {showRulesLink && <Field label="Ссылка на правила" value={settings.requirements.rules_link} disabled={!!why("rules_link")} hint={why("rules_link")} onChange={(v) => upd(["requirements", "rules_link"], v)} />}
        </div>
        )}
        <EmailGateToggle />
      </Section>

      <LoginAlertCard />

      </Group>

      <Group title="Система" icon={SlidersHorizontal}>
      {/* Backup */}
      {showBackup && (
      <Section title="Резервные копии">
        {(can("backup_enabled") || can("backup_send_to_chat")) && (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {can("backup_enabled") && <Toggle label="Автобэкап" sub={why("backup_enabled")} disabled={!!why("backup_enabled")} checked={settings.backup.enabled} onChange={(v) => upd(["backup", "enabled"], v)} />}
          {can("backup_send_to_chat") && <Toggle label="Отправлять в чат" sub={why("backup_send_to_chat")} disabled={!!why("backup_send_to_chat")} checked={settings.backup.send_to_chat} onChange={(v) => upd(["backup", "send_to_chat"], v)} />}
        </div>
        )}
        {(can("backup_interval_hours") || can("backup_max_files")) && (
        <div className="grid grid-cols-2 gap-4 pt-1">
          {can("backup_interval_hours") && <Field label="Интервал (часов)" type="number" value={String(settings.backup.interval_hours)} disabled={!!why("backup_interval_hours")} hint={why("backup_interval_hours")} onChange={(v) => upd(["backup", "interval_hours"], Number(v))} />}
          {can("backup_max_files") && <Field label="Макс. файлов" type="number" value={String(settings.backup.max_files)} disabled={!!why("backup_max_files")} hint={why("backup_max_files")} onChange={(v) => upd(["backup", "max_files"], Number(v))} />}
        </div>
        )}
      </Section>
      )}

      {/* Extra */}
      {showExtra && (
      <Section title="Дополнительно">
        {can("trial_channel_guard") && <Toggle label="Охрана канала для триала" sub={why("trial_channel_guard") ?? "Запрещать пробный период без подписки на канал"} disabled={!!why("trial_channel_guard")} checked={settings.extra.trial_channel_guard} onChange={(v) => upd(["extra", "trial_channel_guard"], v)} />}
        <div className="grid gap-2.5 sm:grid-cols-2">
          {can("mini_app_reserve") && <Toggle label="Резервный Mini App" sub={why("mini_app_reserve")} disabled={!!why("mini_app_reserve")} checked={settings.extra.mini_app_reserve} onChange={(v) => upd(["extra", "mini_app_reserve"], v)} />}
          {can("device_single_reset") && <Toggle label="Сброс одного устройства" sub={why("device_single_reset")} disabled={!!why("device_single_reset")} checked={settings.extra.device_single_reset.enabled} onChange={(v) => upd(["extra", "device_single_reset", "enabled"], v)} />}
          {can("device_all_reset") && <Toggle label="Сброс всех устройств" sub={why("device_all_reset")} disabled={!!why("device_all_reset")} checked={settings.extra.device_all_reset.enabled} onChange={(v) => upd(["extra", "device_all_reset", "enabled"], v)} />}
          {can("link_reset") && <Toggle label="Сброс ссылки" sub={why("link_reset")} disabled={!!why("link_reset")} checked={settings.extra.link_reset.enabled} onChange={(v) => upd(["extra", "link_reset", "enabled"], v)} />}
        </div>
      </Section>
      )}

      {/* Notifications */}
      {can("notifications") && (
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-fg-muted" />
            <h3 className="text-sm font-semibold text-fg">Уведомления</h3>
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
              Включить все
            </button>
            <button
              type="button"
              onClick={() => setAllNotifications(false)}
              className="rounded-lg border border-border-subtle bg-bg px-3 py-1.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
            >
              Выключить все
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Промо-баннер">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title="Промо-баннер в кабинете" desc="Показывает заметный баннер юзерам в кабинете (объявление/акция). Цену не меняет — для скидок используйте промокоды. Можно нацелить на аудиторию и задать период.">
      <Toggle label="Показывать баннер" sub="По умолчанию выключено" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div>
        <label className="mb-1 block text-xs text-fg-muted">Заголовок</label>
        <input type="text" value={cfg.title} onChange={(e) => patch({ title: e.target.value })} className={inputCls} placeholder="Например: Летняя акция!" />
      </div>
      <div>
        <label className="mb-1 block text-xs text-fg-muted">Текст</label>
        <textarea value={cfg.text} onChange={(e) => patch({ text: e.target.value })} className={inputCls} rows={2} placeholder="Скидка 20% на годовой тариф до конца недели" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Текст кнопки (необяз.)</label>
          <input type="text" value={cfg.cta_text} onChange={(e) => patch({ cta_text: e.target.value })} className={inputCls} placeholder="Оформить" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Ссылка кнопки</label>
          <input type="text" value={cfg.cta_url} onChange={(e) => patch({ cta_url: e.target.value })} className={inputCls} placeholder="/billing" />
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Цвет</label>
          <select value={cfg.color} onChange={(e) => patch({ color: e.target.value as PromoBannerConfig["color"] })} className={inputCls}>
            <option value="accent">Акцент</option>
            <option value="red">Красный</option>
            <option value="green">Зелёный</option>
            <option value="amber">Жёлтый</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Кому показывать</label>
          <select value={cfg.audience} onChange={(e) => patch({ audience: e.target.value as PromoBannerConfig["audience"] })} className={inputCls}>
            <option value="all">Всем</option>
            <option value="no_sub">Без подписки</option>
            <option value="has_sub">С подпиской</option>
            <option value="trial">На пробном</option>
            <option value="expiring">Истекающим (≤3 дн.)</option>
          </select>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Показывать с (необяз.)</label>
          <input type="datetime-local" value={cfg.starts_at ? cfg.starts_at.slice(0, 16) : ""} onChange={(e) => patch({ starts_at: e.target.value ? new Date(e.target.value).toISOString() : "" })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Показывать до (необяз.)</label>
          <input type="datetime-local" value={cfg.ends_at ? cfg.ends_at.slice(0, 16) : ""} onChange={(e) => patch({ ends_at: e.target.value ? new Date(e.target.value).toISOString() : "" })} className={inputCls} />
        </div>
      </div>
      <Toggle label="Разрешить скрывать" sub="Юзер может закрыть баннер крестиком" checked={cfg.dismissible} onChange={(v) => patch({ dismissible: v })} />
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

export function ReserveCard() {
  const [cfg, setCfg] = useState<ReserveConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    reserveAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Резервный доступ">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
  const modes = cfg.modes ?? [];
  const modeDesc = modes.find((m) => m.value === cfg.mode)?.desc;
  // У нашего бэкенда сквад-резерв необязателен (пусто = оставить текущий), у бота
  // с режимами — наоборот, без него резерв не запустится. Подпись обязана
  // говорить правду того бэкенда, который сейчас отвечает.
  const squadHint = why("squad_uuid") ?? (graceMode ? "Обязателен для режима «Включён»: резерв выдаётся только на этом скваде." : "");

  return (
    <Section
      title="Резервный доступ истёкшим"
      desc={
        graceMode
          ? "Когда подписка заканчивается или кончается трафик, человек ещё какое-то время сохраняет небольшой лимит («спасательный круг»), чтобы успеть продлить. Дальше доступ отключается. Экран правит настройки самого бота — срок у него считается в ЧАСАХ, а резерв выдаётся на отдельных сквадах."
          : "Когда подписка заканчивается, юзер N дней сохраняет небольшой лимит трафика («спасательный круг»), чтобы успеть продлить. Дальше — доступ отключается. Надпись «подписка закончилась / продлите» приходит из настроек панели (уже по-русски)."
      }
    >
      {can("enabled") && (
        <Toggle
          label="Включить резерв"
          sub={why("enabled") ?? "По умолчанию выключено (раздаёт бесплатный трафик)"}
          checked={cfg.enabled}
          onChange={(v) => patch({ enabled: v })}
          disabled={!!why("enabled")}
        />
      )}
      {graceMode && can("mode") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Режим резерва</label>
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
            <label className="mb-1 block text-xs text-fg-muted">Резерв трафика, ГБ</label>
            <input type="number" min={1} max={graceMode ? undefined : 100} disabled={!!why("reserve_gb")} value={String(cfg.reserve_gb)} onChange={(e) => patch({ reserve_gb: Number(e.target.value) })} className={`${inputCls} ${why("reserve_gb") ? "cursor-not-allowed opacity-60" : ""}`} />
            {why("reserve_gb") && <p className="mt-1 text-xs leading-snug text-fg-muted">{why("reserve_gb")}</p>}
          </div>
        )}
        {can("window_days") && (
          <div>
            <label className="mb-1 block text-xs text-fg-muted">Окно резерва, дней</label>
            <input type="number" min={1} max={60} disabled={!!why("window_days")} value={String(cfg.window_days)} onChange={(e) => patch({ window_days: Number(e.target.value) })} className={`${inputCls} ${why("window_days") ? "cursor-not-allowed opacity-60" : ""}`} />
            {why("window_days") && <p className="mt-1 text-xs leading-snug text-fg-muted">{why("window_days")}</p>}
          </div>
        )}
        {hasHours && can("window_hours") && (
          <div>
            {/* Единицу подписываем явно и не пересчитываем: столько часов и уйдёт в бота. */}
            <label className="mb-1 block text-xs text-fg-muted">Срок резерва, часов</label>
            <input type="number" min={1} disabled={!!why("window_hours")} value={String(cfg.window_hours)} onChange={(e) => patch({ window_hours: Number(e.target.value) })} className={`${inputCls} ${why("window_hours") ? "cursor-not-allowed opacity-60" : ""}`} />
            <p className="mt-1 text-xs leading-snug text-fg-muted">
              {why("window_hours") ?? `Столько часов держится резерв (${cfg.window_hours} ч ≈ ${Math.round(((cfg.window_hours ?? 0) / 24) * 10) / 10} сут.)`}
            </p>
          </div>
        )}
      </div>
      {can("squad_uuid") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">
            {graceMode ? "Сквад для истёкших (UUID)" : "Сквад-резерв (UUID, необязательно — пусто = оставить текущий)"}
          </label>
          <input type="text" disabled={!!why("squad_uuid")} value={cfg.squad_uuid} onChange={(e) => patch({ squad_uuid: e.target.value })} placeholder="напр. 03542796-2d7d-…" className={`${inputCls} ${why("squad_uuid") ? "cursor-not-allowed opacity-60" : ""}`} />
          {squadHint && <p className="mt-1 text-xs leading-snug text-fg-muted">{squadHint}</p>}
        </div>
      )}
      {cfg.squad_uuid_limited !== undefined && can("squad_uuid_limited") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Сквад для исчерпавших трафик (UUID)</label>
          <input type="text" disabled={!!why("squad_uuid_limited")} value={cfg.squad_uuid_limited} onChange={(e) => patch({ squad_uuid_limited: e.target.value })} placeholder="напр. 03542796-2d7d-…" className={`${inputCls} ${why("squad_uuid_limited") ? "cursor-not-allowed opacity-60" : ""}`} />
          <p className="mt-1 text-xs leading-snug text-fg-muted">
            {why("squad_uuid_limited") ?? "Кто выбрал весь трафик — попадает сюда, а не к истёкшим."}
          </p>
        </div>
      )}
      {cfg.trial_enabled !== undefined && (
        <div className="space-y-2.5">
          <p className="text-xs text-fg-muted">Кому положен резерв (обычные платные подписки получают его всегда):</p>
          {can("trial_enabled") && (
            <Toggle label="Пробным подпискам" sub={why("trial_enabled")} checked={!!cfg.trial_enabled} onChange={(v) => patch({ trial_enabled: v })} disabled={!!why("trial_enabled")} />
          )}
          {cfg.daily_enabled !== undefined && can("daily_enabled") && (
            <Toggle label="Суточным подпискам" sub={why("daily_enabled")} checked={!!cfg.daily_enabled} onChange={(v) => patch({ daily_enabled: v })} disabled={!!why("daily_enabled")} />
          )}
          {cfg.free_enabled !== undefined && can("free_enabled") && (
            <Toggle label="Бесплатным тарифам" sub={why("free_enabled")} checked={!!cfg.free_enabled} onChange={(v) => patch({ free_enabled: v })} disabled={!!why("free_enabled")} />
          )}
        </div>
      )}
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

function EmailGateToggle() {
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
      label="Подтверждение email перед покупкой"
      sub={busy ? "Сохранение…" : "Email-юзер должен подтвердить email до триала/оплаты (Telegram/OAuth не касается)"}
      checked={enabled}
      onChange={toggle}
    />
  );
}

export function FreezeCard() {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Заморозка подписки">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title="Заморозка (пауза) подписки" desc="Юзер может поставить подписку на паузу — дни не сгорают, доступ отключается. При возобновлении срок сдвигается на остаток. Максимум N дней паузы, потом авто-возобновление.">
      <Toggle label="Разрешить заморозку" sub="По умолчанию выключено" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="sm:max-w-xs">
        <label className="mb-1 block text-xs text-fg-muted">Макс. длительность паузы, дней</label>
        <input type="number" min={1} max={365} value={String(cfg.max_days)} onChange={(e) => patch({ max_days: Number(e.target.value) })} className={inputCls} />
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

export function NewDeviceCard() {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Новое устройство">{error ?? "Ошибка"}</Section>;

  return (
    <Section title="Уведомление «новое устройство»" desc="Когда к подписке юзера подключается новое устройство — ему приходит уведомление в Telegram/Push (доверие + антишеринг). Первый снимок = базовый, без уведомлений.">
      <Toggle label="Включить уведомление" sub={saving ? "Сохранение…" : (saved ? "Сохранено" : "По умолчанию выключено")} checked={cfg.enabled} onChange={(v) => save(v)} />
      {error && <span className="text-xs text-danger">{error}</span>}
    </Section>
  );
}

export function LoginAlertCard() {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Алерт о новом входе">{error ?? "Ошибка"}</Section>;

  return (
    <Section title="Алерт о новом входе" desc="Когда в аккаунт заходят с нового IP (не первый вход, VPN-ноды исключены) — пользователю приходит уведомление в Telegram / Push / email со сменой пароля при необходимости.">
      <Toggle label="Включить уведомление о новом входе" sub={saving ? "Сохранение…" : (saved ? "Сохранено" : "По умолчанию выключено")} checked={cfg.enabled} onChange={(v) => save(v)} />
      {error && <span className="text-xs text-danger">{error}</span>}
    </Section>
  );
}

export function TrafficAlertCard() {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Трафик заканчивается">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title="Уведомление «трафик заканчивается»" desc="Когда юзер израсходовал заданный % лимита трафика — приходит предупреждение в Telegram/Push, чтобы успел продлить/сменить тариф. Только тарифы с лимитом.">
      <Toggle label="Включить уведомление" sub="По умолчанию выключено" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="sm:max-w-xs">
        <label className="mb-1 block text-xs text-fg-muted">Порог, % израсходованного (50–99)</label>
        <input type="number" min={50} max={99} value={String(cfg.threshold_percent)} onChange={(e) => patch({ threshold_percent: Number(e.target.value) })} className={inputCls} />
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

export function DigestCard() {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Месячный дайджест">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title="Месячный дайджест пользователю" desc="Раз в месяц юзеру приходит сводка в Telegram/Push: сколько ГБ использовал за месяц и любимый сервер. Данные из Remnawave.">
      <Toggle label="Включить дайджест" sub="По умолчанию выключено" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">День месяца (1–28)</label>
          <input type="number" min={1} max={28} value={String(cfg.day_of_month)} onChange={(e) => patch({ day_of_month: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Час (UTC, 0–23)</label>
          <input type="number" min={0} max={23} value={String(cfg.hour)} onChange={(e) => patch({ hour: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

export function WinbackCard() {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Win-back истёкших">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title="Win-back истёкших" desc="Через N дней ПОСЛЕ окончания подписки юзеру приходит напоминание в Telegram/Push «вернись, вот скидка» + одноразовая скидка на возврат (применяется автоматически при следующей покупке).">
      <Toggle label="Включить win-back" sub="По умолчанию выключено" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Скидка, %</label>
          <input type="number" min={1} max={100} value={String(cfg.percent)} onChange={(e) => patch({ percent: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Через сколько дней после окончания</label>
          <input type="number" min={1} max={90} value={String(cfg.days_after)} onChange={(e) => patch({ days_after: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Срок жизни промо, часов</label>
          <input type="number" min={1} max={1440} value={String(cfg.lifetime_hours)} onChange={(e) => patch({ lifetime_hours: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

export function TrialDiscountCard() {
  const [cfg, setCfg] = useState<TrialDiscountConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    trialDiscountAdminApi.get().then(setCfg).catch((e) => setError(loadError(e))).finally(() => setLoading(false));
  }, []);

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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Скидка триальщикам">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title="Скидка триальщикам на первую покупку" desc="За N дней до конца пробного периода юзеру выдаётся одноразовая скидка на первую оплату + баннер-таймер в кабинете и напоминание в Telegram. Скидка гаснет после покупки или по истечении срока.">
      {/* Бэкенд рассказал, что у него это устроено иначе (промокод вместо
          молчаливой выдачи) — печатаем дословно: описание выше написано про наш. */}
      {cfg.note && (
        <p className="rounded-xl border border-border-subtle bg-bg px-4 py-3 text-xs leading-relaxed text-fg-muted">
          {cfg.note}
        </p>
      )}
      <Toggle label="Включить" sub="По умолчанию выключено" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Скидка, %</label>
          <input type="number" min={1} max={100} value={String(cfg.percent)} onChange={(e) => patch({ percent: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">За сколько дней до конца триала</label>
          <input type="number" min={1} max={30} value={String(cfg.days_before)} onChange={(e) => patch({ days_before: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Срок жизни промо, часов</label>
          <input type="number" min={1} max={720} value={String(cfg.lifetime_hours)} onChange={(e) => patch({ lifetime_hours: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

export function MorningSummaryCard() {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Утренняя сводка">{error ?? "Ошибка"}</Section>;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <Section title="Сводка владельцу в Telegram" desc="Раз в сутки владельцу приходит сообщение: выручка, новые регистрации, активные подписки, сколько истекает в ближайшие дни.">
      <Toggle label="Включить сводку" sub="Отправлять ежедневно в Telegram владельцу" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Час отправки (0–23, время сервера)</label>
          <input type="number" min={0} max={23} value={String(cfg.hour)} onChange={(e) => patch({ hour: Number(e.target.value) })} className={inputCls} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Окно «истекают в N дней»</label>
          <input type="number" min={1} value={String(cfg.expiring_days)} onChange={(e) => patch({ expiring_days: Number(e.target.value) })} className={inputCls} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
        </span>
        <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>
    </Section>
  );
}

export function TopupSettingsCard({ applicability }: { applicability?: TopupApplicability } = {}) {
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
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (error === UNSUPPORTED) return null;
  if (!cfg) return <Section title="Пополнение баланса">{error ?? "Ошибка"}</Section>;

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
      title="Пополнение через шлюзы"
      desc={
        can("bonus_percent")
          ? "Пользователь платит через платёжный шлюз, сумма (+бонус) зачисляется на ₽-баланс. Только рублёвые шлюзы."
          : "Пользователь платит через платёжный шлюз, сумма зачисляется на ₽-баланс. Только рублёвые шлюзы."
      }
    >
      {can("enabled") && (
        <Toggle
          label="Включить пополнение"
          sub={why("enabled") ?? "Показывать блок пополнения в кабинете"}
          checked={cfg.enabled}
          disabled={!!why("enabled")}
          onChange={(v) => patch({ enabled: v })}
        />
      )}
      {numberFields > 0 && (
        <div className={`grid gap-3 ${numberFields >= 3 ? "sm:grid-cols-3" : numberFields === 2 ? "sm:grid-cols-2" : ""}`}>
          {can("bonus_percent") && (
            <div>
              <label className="mb-1 block text-xs text-fg-muted">Бонус, %</label>
              <input type="number" min={0} max={100} disabled={!!why("bonus_percent")} value={String(cfg.bonus_percent)} onChange={(e) => patch({ bonus_percent: Number(e.target.value) })} className={inputCls} />
              {lockNote("bonus_percent")}
            </div>
          )}
          {can("min_amount") && (
            <div>
              <label className="mb-1 block text-xs text-fg-muted">Мин. сумма, ₽</label>
              <input type="number" min={1} disabled={!!why("min_amount")} value={String(cfg.min_amount)} onChange={(e) => patch({ min_amount: Number(e.target.value) })} className={inputCls} />
              {lockNote("min_amount")}
            </div>
          )}
          {can("max_amount") && (
            <div>
              <label className="mb-1 block text-xs text-fg-muted">Макс. сумма, ₽</label>
              <input type="number" min={1} disabled={!!why("max_amount")} value={String(cfg.max_amount)} onChange={(e) => patch({ max_amount: Number(e.target.value) })} className={inputCls} />
              {lockNote("max_amount")}
            </div>
          )}
        </div>
      )}
      {can("presets") && (
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Пресеты сумм (через запятую)</label>
          <input type="text" disabled={!!why("presets")} value={cfg.presets.join(", ")} onChange={(e) => patch({ presets: e.target.value.split(",").map((s) => Number(s.trim())).filter((n) => !Number.isNaN(n)) })} className={inputCls} />
          {lockNote("presets")}
        </div>
      )}
      <div className="flex items-center justify-between gap-3 pt-1">
        <span className="text-xs">
          {error && <span className="text-danger">{error}</span>}
          {saved && <span className="text-success">Сохранено</span>}
          {readonly && (
            <span className="text-fg-muted">
              Условия берутся из настроек платёжных методов бота — меняйте их там.
            </span>
          )}
        </span>
        {/* Кнопки нет, когда править нечем: у бэкенда, где каждое поле помечено
            «правится не отсюда», «Сохранить» вело бы в отказ. Карт нет — бэкенд
            наш, кнопка на месте. */}
        {!readonly && anyEditable && (
          <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
            <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
          </button>
        )}
      </div>
    </Section>
  );
}
