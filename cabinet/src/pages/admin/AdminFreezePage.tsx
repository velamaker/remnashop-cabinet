import { Snowflake } from "lucide-react";
import { useT } from "@/i18n/I18nContext";
import { FreezeCard } from "./AdminSettingsPage";

// «Заморозка подписки» — отдельная страница (раздел Продажи).
export default function AdminFreezePage() {
  const t = useT();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Snowflake className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.freeze.title")}</h1>
      </div>
      <FreezeCard />
    </div>
  );
}
