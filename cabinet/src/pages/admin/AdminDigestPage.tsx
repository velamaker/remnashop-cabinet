import { BarChart3 } from "lucide-react";
import { DigestCard } from "./AdminSettingsPage";

// «Месячный дайджест» — вынесен из «Настроек» в раздел Маркетинг.
export default function AdminDigestPage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <BarChart3 className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Месячный дайджест</h1>
      </div>
      <DigestCard />
    </div>
  );
}
