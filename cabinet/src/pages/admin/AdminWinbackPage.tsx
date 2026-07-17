import { Undo2 } from "lucide-react";
import { WinbackCard } from "./AdminSettingsPage";

// «Win-back истёкших» — вынесен из «Настроек» в раздел Маркетинг.
export default function AdminWinbackPage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Undo2 className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Win-back истёкших</h1>
      </div>
      <WinbackCard />
    </div>
  );
}
