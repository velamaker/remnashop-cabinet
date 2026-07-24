import { useEffect, useState, useCallback } from "react";
import { DownloadCloud, UploadCloud, RefreshCw, Server, Database, CheckCircle2, AlertCircle } from "lucide-react";
import { importAdminApi } from "@/api/admin";
import { ApiError } from "@/types/api";

type Msg = { ok: boolean; text: string } | null;

export default function AdminImportPage() {
  const [status, setStatus] = useState<{ panel: boolean; bot: boolean; xui: boolean }>({ panel: false, bot: false, xui: false });
  const [squads, setSquads] = useState<{ uuid: string; name: string }[]>([]);
  const [selSquads, setSelSquads] = useState<Set<string>>(new Set());
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Record<string, Msg>>({});

  const loadStatus = useCallback(() => {
    importAdminApi.status().then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    loadStatus();
    importAdminApi.squads().then((r) => setSquads(r.squads)).catch(() => {});
    const id = setInterval(loadStatus, 5000);
    return () => clearInterval(id);
  }, [loadStatus]);

  const setM = (k: string, m: Msg) => setMsg((p) => ({ ...p, [k]: m }));

  const runSync = async (kind: "panel" | "bot") => {
    setBusy(kind);
    setM(kind, null);
    try {
      const r = kind === "panel" ? await importAdminApi.syncPanel() : await importAdminApi.syncBot();
      setM(kind, { ok: true, text: `Синхронизировано пользователей: ${r.synced}` });
    } catch (e) {
      setM(kind, { ok: false, text: e instanceof ApiError ? e.detail : "Ошибка" });
    } finally {
      setBusy(null);
      loadStatus();
    }
  };

  const runXui = async () => {
    if (!file || selSquads.size === 0) return;
    setBusy("xui");
    setM("xui", null);
    try {
      const r = await importAdminApi.xui(file, [...selSquads]);
      setM("xui", { ok: true, text: `Найдено ${r.found} пользователей — импорт запущен в фоне` });
      setFile(null);
    } catch (e) {
      setM("xui", { ok: false, text: e instanceof ApiError ? e.detail : "Ошибка" });
    } finally {
      setBusy(null);
      loadStatus();
    }
  };

  const Result = ({ m }: { m: Msg }) =>
    m ? (
      <p className={`mt-2 flex items-center gap-1.5 text-sm ${m.ok ? "text-success" : "text-danger"}`}>
        {m.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
        {m.text}
      </p>
    ) : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-fg">Импорт пользователей</h1>
        <p className="mt-1 text-sm text-fg-muted">Синхронизация с панелью Remnawave и миграция из x-ui — как в боте.</p>
      </div>

      {/* Синхронизация из панели */}
      <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="mb-2 flex items-center gap-2">
          <DownloadCloud className="h-5 w-5 text-accent" />
          <h2 className="text-base font-semibold text-fg">Из панели Remnawave → в бота</h2>
        </div>
        <p className="mb-3 text-sm text-fg-muted">Подтянуть пользователей, которые есть в панели, но отсутствуют в базе бота.</p>
        <button
          onClick={() => runSync("panel")}
          disabled={busy !== null || status.panel}
          className="btn-gradient inline-flex items-center gap-2 rounded-xl border-0 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${busy === "panel" || status.panel ? "animate-spin" : ""}`} />
          {busy === "panel" || status.panel ? "Синхронизация…" : "Синхронизировать из панели"}
        </button>
        <Result m={msg.panel ?? null} />
      </div>

      {/* Синхронизация бот → панель */}
      <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="mb-2 flex items-center gap-2">
          <UploadCloud className="h-5 w-5 text-accent" />
          <h2 className="text-base font-semibold text-fg">Из бота → в панель Remnawave</h2>
        </div>
        <p className="mb-3 text-sm text-fg-muted">Отправить пользователей бота в панель (создать недостающих).</p>
        <button
          onClick={() => runSync("bot")}
          disabled={busy !== null || status.bot}
          className="inline-flex items-center gap-2 rounded-xl border border-accent/40 bg-accent-subtle px-4 py-2 text-sm font-semibold text-accent disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${busy === "bot" || status.bot ? "animate-spin" : ""}`} />
          {busy === "bot" || status.bot ? "Синхронизация…" : "Синхронизировать в панель"}
        </button>
        <Result m={msg.bot ?? null} />
      </div>

      {/* Импорт из файла x-ui */}
      <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="mb-2 flex items-center gap-2">
          <Database className="h-5 w-5 text-accent" />
          <h2 className="text-base font-semibold text-fg">Импорт из файла x-ui / 3x-ui</h2>
        </div>
        <p className="mb-3 text-sm text-fg-muted">Загрузите файл БД x-ui (.db) — пользователи будут созданы в выбранных сквадах.</p>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-muted">Файл БД x-ui</label>
            <input
              type="file"
              accept=".db,.sqlite,.sqlite3"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-fg-muted file:mr-3 file:rounded-lg file:border-0 file:bg-accent file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-accent-fg hover:file:opacity-90"
            />
          </div>

          <div>
            <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-fg-muted"><Server className="h-3.5 w-3.5" /> Сквады (куда создать)</label>
            {squads.length === 0 ? (
              <p className="text-sm text-fg-subtle">Сквады не найдены (проверьте связь с панелью).</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {squads.map((s) => {
                  const on = selSquads.has(s.uuid);
                  return (
                    <button
                      key={s.uuid}
                      type="button"
                      onClick={() => setSelSquads((p) => { const n = new Set(p); if (n.has(s.uuid)) n.delete(s.uuid); else n.add(s.uuid); return n; })}
                      className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${on ? "border-accent bg-accent-subtle text-accent" : "border-border-subtle bg-bg-raised text-fg-muted hover:border-[var(--border)]"}`}
                    >
                      {s.name}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <button
            onClick={runXui}
            disabled={busy !== null || status.xui || !file || selSquads.size === 0}
            className="btn-gradient inline-flex items-center gap-2 rounded-xl border-0 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            <UploadCloud className="h-4 w-4" />
            {busy === "xui" || status.xui ? "Импорт…" : "Импортировать"}
          </button>
          <Result m={msg.xui ?? null} />
        </div>
      </div>
    </div>
  );
}
