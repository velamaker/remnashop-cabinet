import { Sunrise } from "lucide-react";
import { MorningSummaryCard } from "./AdminSettingsPage";

// Раздел «Утренняя сводка» — вынесен из «Настроек» в отдельный пункт панели.
export default function AdminMorningSummaryPage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Sunrise className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Утренняя сводка</h1>
      </div>
      <MorningSummaryCard />
    </div>
  );
}
