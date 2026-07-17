import { useRef, useState } from "react";
import { Download, Upload, Database, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { settingsIoAdminApi, type SettingsBundle } from "@/api/admin";
import { ApiError } from "@/types/api";

// Импорт/экспорт настроек инсталляции (только владелец). Бэкап конфигурации одним файлом.
export default function AdminBackupPage() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const doExport = async () => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const bundle = await settingsIoAdminApi.export();
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `remnashop-settings-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMsg(`Экспортировано разделов: ${Object.keys(bundle.assets || {}).length}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Ошибка экспорта");
    } finally {
      setBusy(false);
    }
  };

  const doImport = async (file: File) => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const text = await file.text();
      const bundle = JSON.parse(text) as SettingsBundle;
      if (!bundle || typeof bundle !== "object" || !bundle.assets) {
        throw new Error("Файл не похож на бэкап настроек");
      }
      if (!confirm("Импортировать настройки из файла? Текущие конфиги будут перезаписаны.")) {
        setBusy(false);
        return;
      }
      const res = await settingsIoAdminApi.import(bundle);
      setMsg(`Восстановлено разделов: ${res.count}${res.skipped.length ? `, пропущено: ${res.skipped.length}` : ""}. Обновите страницу (иногда нужен hard-reload).`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "Ошибка импорта");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Database className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Импорт / экспорт настроек</h1>
      </div>

      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <p className="text-sm text-fg-muted">
          Бэкап всей конфигурации инсталляции одним файлом: оформление, приложения, меню, почта,
          вход, все настройки фич. <b className="text-fg">Только владелец.</b> Файл содержит секреты
          (SMTP/OIDC) — храните безопасно. Рантайм-данные и приватный push-ключ в бэкап не входят.
        </p>

        <div className="mt-4 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={doExport}
            disabled={busy}
            className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Экспортировать
          </button>

          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            className="inline-flex items-center gap-2 rounded-xl border border-border-subtle bg-bg px-4 py-2.5 text-sm font-medium text-fg hover:bg-bg-raised disabled:opacity-50"
          >
            <Upload className="h-4 w-4" />
            Импортировать из файла
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) doImport(f);
            }}
          />
        </div>

        {msg && (
          <p className="mt-3 flex items-center gap-1.5 text-sm text-success">
            <CheckCircle2 className="h-4 w-4" /> {msg}
          </p>
        )}
        {err && (
          <p className="mt-3 flex items-center gap-1.5 text-sm text-danger">
            <AlertCircle className="h-4 w-4" /> {err}
          </p>
        )}
      </section>
    </div>
  );
}
