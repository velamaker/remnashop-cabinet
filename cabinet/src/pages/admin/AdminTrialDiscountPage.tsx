import { Percent } from "lucide-react";
import { useT } from "@/i18n/I18nContext";
import { TrialDiscountCard } from "./AdminSettingsPage";

// «Скидка триальщикам» — вынесена из «Настроек» в раздел Маркетинг.
export default function AdminTrialDiscountPage() {
  const t = useT();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Percent className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.trialdiscount.title")}</h1>
      </div>
      <TrialDiscountCard />
    </div>
  );
}
