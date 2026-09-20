import { BarChart3 } from "lucide-react";
import { useT } from "@/i18n/I18nContext";
import { DigestCard, DigestEmailCard } from "./AdminSettingsPage";

// «Месячный дайджест» — вынесен из «Настроек» в раздел Маркетинг.
export default function AdminDigestPage() {
  const t = useT();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <BarChart3 className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.digest.title")}</h1>
      </div>
      <DigestCard />
      <DigestEmailCard />
    </div>
  );
}
