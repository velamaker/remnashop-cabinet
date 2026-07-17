import { Umbrella } from "lucide-react";
import { ReserveCard } from "./AdminSettingsPage";

// «Резервный доступ истёкшим» — вынесен из «Настроек».
export default function AdminReservePage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Umbrella className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Резервный доступ истёкшим</h1>
      </div>
      <ReserveCard />
    </div>
  );
}
