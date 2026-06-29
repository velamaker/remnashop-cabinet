import { useEffect, useMemo, useState } from "react";
import { Save, AlertCircle, CheckCircle2, Bell, Lock, Coins, SlidersHorizontal } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { settingsAdminApi, type AdminSettings } from "@/api/admin";
import { ApiError } from "@/types/api";

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

/** A full clickable row: label/sub on the left, switch pinned right inside a contained card. */
function Toggle({
  label,
  sub,
  checked,
  onChange,
}: {
  label: string;
  sub?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`flex w-full items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
        checked
          ? "border-accent/30 bg-accent/5 hover:bg-accent/10"
          : "border-border-subtle bg-bg hover:bg-bg-subtle"
      }`}
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-fg">{label}</p>
        {sub && <p className="mt-0.5 text-xs leading-snug text-fg-muted">{sub}</p>}
      </div>
      <Switch checked={checked} onChange={onChange} />
    </button>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-fg-muted">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
      />
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

export default function AdminSettingsPage() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
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

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await settingsAdminApi.update({
        access: settings.access,
        registration_allowed: settings.access.registration_allowed,
        payments_allowed: settings.access.payments_allowed,
        rules_required: settings.requirements.rules_required,
        channel_required: settings.requirements.channel_required,
        channel_link: settings.requirements.channel_link,
        rules_link: settings.requirements.rules_link,
        referral: {
          enable: settings.referral.enable,
          reward_type: settings.referral.reward.type,
          reward_value: Object.values(settings.referral.reward.config)[0] ?? 0,
          accrual_strategy: settings.referral.accrual_strategy,
        },
        backup: settings.backup,
        trial_channel_guard: settings.extra.trial_channel_guard,
        mini_app_reserve: settings.extra.mini_app_reserve,
        notifications: settings.notifications,
      });
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
      const copy = JSON.parse(JSON.stringify(prev)) as AdminSettings;
      for (const key of Object.keys(copy.notifications)) {
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

  const firstRewardValue = Object.values(settings.referral.reward.config)[0] ?? 0;

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      {/* Sticky header */}
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-border-subtle bg-bg/80 px-5 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <h1 className="text-xl font-bold text-fg md:text-2xl">Настройки</h1>
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
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      <Group title="Доступ и регистрация" icon={Lock}>
      {/* Access */}
      <Section title="Доступ" desc="Кто и как может пользоваться сервисом">
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
        <div className="grid gap-2.5 sm:grid-cols-2">
          <Toggle label="Разрешить регистрацию" checked={settings.access.registration_allowed} onChange={(v) => upd(["access", "registration_allowed"], v)} />
          <Toggle label="Разрешить оплату" checked={settings.access.payments_allowed} onChange={(v) => upd(["access", "payments_allowed"], v)} />
        </div>
      </Section>

      {/* Requirements */}
      <Section title="Требования" desc="Условия для пользователей при регистрации">
        <Toggle label="Принять правила" sub="Пользователь должен принять правила при регистрации" checked={settings.requirements.rules_required} onChange={(v) => upd(["requirements", "rules_required"], v)} />
        <Toggle label="Обязательный канал" sub="Пользователь должен подписаться на канал" checked={settings.requirements.channel_required} onChange={(v) => upd(["requirements", "channel_required"], v)} />
        <div className="grid gap-4 pt-1 sm:grid-cols-2">
          <Field label="Ссылка на канал" value={settings.requirements.channel_link} onChange={(v) => upd(["requirements", "channel_link"], v)} />
          <Field label="Ссылка на правила" value={settings.requirements.rules_link} onChange={(v) => upd(["requirements", "rules_link"], v)} />
        </div>
      </Section>

      </Group>

      <Group title="Монетизация и рефералы" icon={Coins}>
      {/* Referral */}
      <Section title="Реферальная программа">
        <Toggle label="Реферальная программа" sub="Включить начисление наград за приглашённых" checked={settings.referral.enable} onChange={(v) => upd(["referral", "enable"], v)} />
        <div className="grid gap-4 pt-1 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-muted">Тип награды</label>
            <select
              value={settings.referral.reward.type}
              onChange={(e) => upd(["referral", "reward", "type"], e.target.value)}
              className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="EXTRA_DAYS">Дополнительные дни</option>
              <option value="POINTS">Баллы</option>
            </select>
          </div>
          <Field
            label={settings.referral.reward.type === "EXTRA_DAYS" ? "Дней за реферала" : "Баллов за реферала"}
            type="number"
            value={String(firstRewardValue)}
            onChange={(v) => upd(["referral", "reward", "config"], { FIRST: Number(v) })}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-muted">Стратегия начисления</label>
          <select
            value={settings.referral.accrual_strategy}
            onChange={(e) => upd(["referral", "accrual_strategy"], e.target.value)}
            className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="ON_FIRST_PAYMENT">При первой оплате реферала</option>
            <option value="ON_REGISTRATION">При регистрации</option>
          </select>
        </div>
      </Section>

      </Group>

      <Group title="Система" icon={SlidersHorizontal}>
      {/* Backup */}
      <Section title="Резервные копии">
        <div className="grid gap-2.5 sm:grid-cols-2">
          <Toggle label="Автобэкап" checked={settings.backup.enabled} onChange={(v) => upd(["backup", "enabled"], v)} />
          <Toggle label="Отправлять в чат" checked={settings.backup.send_to_chat} onChange={(v) => upd(["backup", "send_to_chat"], v)} />
        </div>
        <div className="grid grid-cols-2 gap-4 pt-1">
          <Field label="Интервал (часов)" type="number" value={String(settings.backup.interval_hours)} onChange={(v) => upd(["backup", "interval_hours"], Number(v))} />
          <Field label="Макс. файлов" type="number" value={String(settings.backup.max_files)} onChange={(v) => upd(["backup", "max_files"], Number(v))} />
        </div>
      </Section>

      {/* Extra */}
      <Section title="Дополнительно">
        <Toggle label="Охрана канала для триала" sub="Запрещать пробный период без подписки на канал" checked={settings.extra.trial_channel_guard} onChange={(v) => upd(["extra", "trial_channel_guard"], v)} />
        <div className="grid gap-2.5 sm:grid-cols-2">
          <Toggle label="Резервный Mini App" checked={settings.extra.mini_app_reserve} onChange={(v) => upd(["extra", "mini_app_reserve"], v)} />
          <Toggle label="Сброс одного устройства" checked={settings.extra.device_single_reset.enabled} onChange={(v) => upd(["extra", "device_single_reset", "enabled"], v)} />
          <Toggle label="Сброс всех устройств" checked={settings.extra.device_all_reset.enabled} onChange={(v) => upd(["extra", "device_all_reset", "enabled"], v)} />
          <Toggle label="Сброс ссылки" checked={settings.extra.link_reset.enabled} onChange={(v) => upd(["extra", "link_reset", "enabled"], v)} />
        </div>
      </Section>

      {/* Notifications */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-fg-muted" />
            <h3 className="text-sm font-semibold text-fg">Уведомления</h3>
            <span className="rounded-full bg-bg px-2 py-0.5 text-xs font-medium text-fg-muted">
              {notifStats.on} / {notifStats.total}
            </span>
          </div>
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
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2">
          {Object.entries(settings.notifications).map(([key, enabled]) => (
            <Toggle key={key} label={prettyNotification(key)} checked={enabled} onChange={(v) => upd(["notifications", key], v)} />
          ))}
        </div>
      </section>
      </Group>
    </div>
  );
}
