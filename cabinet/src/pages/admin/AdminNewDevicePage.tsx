import { Smartphone } from "lucide-react";
import { NewDeviceCard } from "./AdminSettingsPage";

// «Новое устройство подключилось» — уведомление, отдельная страница (раздел Маркетинг).
export default function AdminNewDevicePage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Smartphone className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Новое устройство</h1>
      </div>
      <NewDeviceCard />
    </div>
  );
}
