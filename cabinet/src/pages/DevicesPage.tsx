import { useCallback, useEffect, useState } from "react";
import { Smartphone, Trash2, Laptop, Tablet } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConnectGuide } from "@/components/ConnectGuide";
import type { DeviceResponse, DevicesResponse } from "@/types/api";
import { ApiError } from "@/types/api";

function getDeviceIcon(platform: string | null) {
  const p = (platform || "").toLowerCase();
  if (p.includes("ios") || p.includes("android")) return Smartphone;
  if (p.includes("ipad") || p.includes("tablet")) return Tablet;
  return Laptop;
}

function DeviceRow({
  device,
  onDelete,
}: {
  device: DeviceResponse;
  onDelete: (hwid: string) => void;
}) {
  const [isDeleting, setIsDeleting] = useState(false);
  const Icon = getDeviceIcon(device.platform);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await subscriptionApi.deleteDevice(device.hwid);
      onDelete(device.hwid);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="flex items-center gap-3 rounded-xl border border-border-subtle bg-bg-subtle p-3">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-bg-raised text-fg-muted">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-fg">
          {device.device_model || device.platform || "Неизвестное устройство"}
        </p>
        <p className="truncate text-xs text-fg-subtle">
          {device.os_version || device.user_agent || device.hwid}
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
      setError(e instanceof ApiError ? e.detail : "Не удалось загрузить устройства");
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
      setError(e instanceof ApiError ? e.detail : "Не удалось очистить устройства");
    } finally {
      setIsClearingAll(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold text-fg">Устройства</h1>

      {subUrl && <ConnectGuide subUrl={subUrl} />}

      <Card>
        <CardHeader
          title={
            data ? `${data.current_count} из ${data.max_count} устройств` : "Устройства"
          }
          subtitle="Удалите устройство, чтобы освободить слот для нового"
          action={
            data && data.devices.length > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={handleClearAll}
                isLoading={isClearingAll}
                className="text-fg-subtle hover:text-danger"
              >
                Очистить все
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
            Пока нет подключённых устройств
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
