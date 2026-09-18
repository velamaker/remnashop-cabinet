import { useCallback, useEffect, useMemo, useState } from "react";
import { Trash2, Layers } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { useT } from "@/i18n/I18nContext";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConnectGuide } from "@/components/ConnectGuide";
import { PlatformIcon } from "@/components/PlatformIcon";
import { DeviceUpsellCard } from "@/components/DeviceUpsellCard";
import { ExtraDevicesPanel } from "@/components/ExtraDevicesPanel";
import { formatDate, formatRelativeOnline } from "@/lib/format";
import type { DeviceResponse, DevicesResponse, SubscriptionInfoResponse } from "@/types/api";
import { ApiError } from "@/types/api";
import { appFromUserAgent, freeableSlots, sameDeviceGroups } from "@/lib/deviceGroups";

type ActivityTone = "online" | "recent" | "idle" | "stale";

function activityTone(iso: string | null): ActivityTone {
  if (!iso) return "stale";
  const minutes = (Date.now() - new Date(iso).getTime()) / 60000;
  if (minutes < 10) return "online";
  if (minutes < 60 * 24) return "recent";
  if (minutes < 60 * 24 * 7) return "idle";
  return "stale";
}

const TONE_COLOR: Record<ActivityTone, string> = {
  online: "var(--success)",
  recent: "var(--success)",
  idle: "var(--warning)",
  stale: "var(--fg-subtle)",
};

function DeviceRow({
  device,
  onDelete,
  showApp = false,
}: {
  device: DeviceResponse;
  onDelete: (hwid: string) => void;
  /** Показать имя приложения: нужно там, где слоты дублируются одним аппаратом —
      иначе строки неразличимы и непонятно, какую удалять. */
  showApp?: boolean;
}) {
  const t = useT();
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await subscriptionApi.deleteDevice(device.hwid);
      onDelete(device.hwid);
    } finally {
      setIsDeleting(false);
    }
  };

  const lastSeen = device.updated_at ?? device.created_at;
  const tone = activityTone(lastSeen);
  const dotColor = TONE_COLOR[tone];

  return (
    <div className="flex items-center gap-3 rounded-xl border border-border-subtle bg-bg-subtle p-3 transition-colors hover:border-accent-subtle">
      <div className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-bg-raised">
        <PlatformIcon
          platform={device.platform}
          model={device.device_model}
          os={device.os_version}
          userAgent={device.user_agent}
          className="h-5 w-5 text-fg-muted"
        />
        <span
          className="absolute -right-0.5 -top-0.5 flex h-2.5 w-2.5"
          title={t("devices.lastActivity")}
        >
          {tone === "online" && (
            <span
              className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
              style={{ backgroundColor: dotColor }}
            />
          )}
          <span
            className="relative inline-flex h-2.5 w-2.5 rounded-full ring-2 ring-bg-subtle"
            style={{ backgroundColor: dotColor }}
          />
        </span>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-fg">
          {device.device_model || device.platform || t("devices.unknown")}
        </p>
        <p className="truncate text-xs text-fg-subtle">
          {showApp && appFromUserAgent(device.user_agent)
            ? `${appFromUserAgent(device.user_agent)} · ${device.os_version || device.hwid}`
            : device.os_version || device.user_agent || device.hwid}
          {/* Дата подключения: когда устройств несколько и они одинаковые, только
              по ней и понятно, какое лишнее. */}
          {device.created_at && (
            <> · {t("devices.connected")} {formatDate(device.created_at)}</>
          )}
        </p>
        <p className="mt-1 flex items-center gap-1.5 text-xs" style={{ color: dotColor }}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: dotColor }} />
          <span className="truncate">
            {tone === "online"
              ? t("devices.activeNow")
              : t("devices.lastActivity") + ": " + formatRelativeOnline(lastSeen)}
          </span>
        </p>
      </div>
      <Button
        size="sm"
        variant="ghost"
        onClick={handleDelete}
        isLoading={isDeleting}
        className="text-fg-subtle hover:text-danger"
      >
        <Trash2 className="h-4 w-4" />
      </Button>
    </div>
  );
}

function SameDeviceHint({ devices }: { devices: DeviceResponse[] }) {
  const t = useT();
  const groups = useMemo(() => sameDeviceGroups(devices), [devices]);
  if (!groups.length) return null;

  return (
    <div className="flex gap-3 rounded-xl border border-warning/30 bg-warning/5 p-3">
      <Layers className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
      <div className="min-w-0 text-sm">
        <p className="font-medium text-fg">{t("devices.sameDeviceTitle")}</p>
        <p className="mt-0.5 text-xs text-fg-muted">
          {t("devices.sameDeviceText", { n: freeableSlots(groups) })}
        </p>
        <ul className="mt-2 space-y-1">
          {groups.map((g) => (
            <li key={g.label} className="text-xs text-fg-subtle">
              <span className="text-fg-muted">{g.label}</span>
              {" — "}
              {/* Имя приложения здесь и есть главное: по нему человек понимает,
                  какой слот лишний. В самой строке устройства его не видно, пока
                  у записи заполнена версия ОС. */}
              {g.apps.join(", ")}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function DevicesPage() {
  const t = useT();
  const [data, setData] = useState<DevicesResponse | null>(null);
  // Подписка целиком: ссылке подключения нужен url, блоку «Нужно больше устройств?» —
  // статус, трафик и срок тарифа.
  const [sub, setSub] = useState<SubscriptionInfoResponse | null>(null);
  const subUrl = sub?.url ?? null;
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isClearingAll, setIsClearingAll] = useState(false);

  useEffect(() => {
    subscriptionApi
      .current()
      .then((s) => setSub(s ?? null))
      .catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await subscriptionApi.devices();
      setData(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("devices.errLoad"));
    } finally {
      setIsLoading(false);
    }
    // t нужен лишь для фолбэка ошибки — лоадер не должен перезапускаться на смене языка
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Слоты, похожие на один аппарат: только у них показываем приложение —
  // в остальных строках это лишний шум.
  const duplicateHwids = useMemo(() => {
    const set = new Set<string>();
    for (const g of sameDeviceGroups(data?.devices ?? [])) {
      for (const d of g.devices) set.add(d.hwid);
    }
    return set;
  }, [data]);

  // Карточка апселла сообщает, показала ли она кнопку докупки: две одинаковые
  // кнопки на одной странице сбивали бы с толку.
  const [cardShowsExtra, setCardShowsExtra] = useState(false);

  const handleDeviceDeleted = (hwid: string) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            devices: prev.devices.filter((d) => d.hwid !== hwid),
            current_count: prev.current_count - 1,
          }
        : prev,
    );
  };

  const handleClearAll = async () => {
    setIsClearingAll(true);
    try {
      await subscriptionApi.deleteAllDevices();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("devices.errClear"));
    } finally {
      setIsClearingAll(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold text-fg">{t("nav.devices")}</h1>

      {subUrl && <ConnectGuide subUrl={subUrl} />}

      <Card>
        <CardHeader
          title={
            data ? t("devices.count", { cur: data.current_count, max: data.max_count }) : t("nav.devices")
          }
          subtitle={t("devices.subtitle")}
          action={
            data && data.devices.length > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={handleClearAll}
                isLoading={isClearingAll}
                className="text-fg-subtle hover:text-danger"
              >
                {t("devices.clearAll")}
              </Button>
            ) : undefined
          }
        />

        {isLoading && (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        )}

        {error && <p className="text-sm text-danger">{error}</p>}

        {!isLoading && data && data.devices.length === 0 && (
          <p className="py-6 text-center text-sm text-fg-subtle">
            {t("devices.empty")}
          </p>
        )}

        {!isLoading && data && data.devices.length > 0 && (
          <div className="flex flex-col gap-2">
            <DeviceUpsellCard
              variant="devices"
              subscription={sub}
              devices={data}
              onChanged={load}
              onOfferShown={setCardShowsExtra}
            />
            {/* Панель докупленных мест тумблеру апселла не подчиняется: продление уже
                оплаченного места — не реклама. Кнопку «Докупить» она показывает только
                когда карточка выше её не показала. */}
            <ExtraDevicesPanel cardShowsOffer={cardShowsExtra} onChanged={load} />
            <SameDeviceHint devices={data.devices} />
            {data.devices.map((device) => (
              <DeviceRow
                key={device.hwid}
                device={device}
                onDelete={handleDeviceDeleted}
                showApp={duplicateHwids.has(device.hwid)}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
