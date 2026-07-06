import { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { useT } from "@/i18n/I18nContext";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConnectGuide } from "@/components/ConnectGuide";
import { PlatformIcon } from "@/components/PlatformIcon";
import { formatRelativeOnline } from "@/lib/format";
import type { DeviceResponse, DevicesResponse } from "@/types/api";
import { ApiError } from "@/types/api";

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
}: {
  device: DeviceResponse;
  onDelete: (hwid: string) => void;
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
          {device.os_version || device.user_agent || device.hwid}
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

export default function DevicesPage() {
  const t = useT();
  const [data, setData] = useState<DevicesResponse | null>(null);
  const [subUrl, setSubUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isClearingAll, setIsClearingAll] = useState(false);

  useEffect(() => {
    subscriptionApi
      .current()
      .then((s) => setSubUrl(s?.url ?? null))
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
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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
            {data.devices.map((device) => (
              <DeviceRow
                key={device.hwid}
                device={device}
                onDelete={handleDeviceDeleted}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
