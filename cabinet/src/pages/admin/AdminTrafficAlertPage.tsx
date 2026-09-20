import { Gauge } from "lucide-react";
import { useT } from "@/i18n/I18nContext";
import { TrafficAlertCard } from "./AdminSettingsPage";

// «Трафик заканчивается» — уведомление, отдельная страница (раздел Маркетинг).
export default function AdminTrafficAlertPage() {
  const t = useT();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Gauge className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.trafficalert.title")}</h1>
      </div>
      <TrafficAlertCard />
    </div>
  );
}
