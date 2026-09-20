import { Megaphone } from "lucide-react";
import { useT } from "@/i18n/I18nContext";
import { PromoBannerCard } from "./AdminSettingsPage";

// «Промо-баннер» — вынесен из «Настроек» в раздел Маркетинг.
export default function AdminPromoBannerPage() {
  const t = useT();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Megaphone className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.promobanner.title")}</h1>
      </div>
      <PromoBannerCard />
    </div>
  );
}
