import { Coins } from "lucide-react";
import { TopupSettingsCard } from "./AdminSettingsPage";

// Раздел «Пополнение баланса» — вынесен из «Настроек» в отдельный пункт панели.
export default function AdminTopupPage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Coins className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Пополнение баланса</h1>
      </div>
      <TopupSettingsCard />
    </div>
  );
}
