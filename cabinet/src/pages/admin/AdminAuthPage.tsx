import { useEffect, useState } from "react";
import { Save, KeyRound, Copy, Check } from "lucide-react";
import { authSettingsAdminApi, type AuthSettings } from "@/api/authSettings";
import { ApiError } from "@/types/api";

// Включение входа/привязки через Telegram (OIDC) прямо из админки: Client ID и
// Secret из @BotFather → Web Login сохраняются в assets/auth.json и применяются
// сразу, без переустановки. Пустой Secret при сохранении = «не менять».
export default function AdminAuthPage() {
  const [s, setS] = useState<AuthSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const apply = (d: AuthSettings) => {
    setS(d);
    setClientId(d.telegram_oidc_client_id);
    setClientSecret("");
    // null (авто) трактуем как «включено, если есть креды» — показываем активное состояние.
    setEnabled(d.telegram_oidc_enabled_setting ?? d.telegram_oidc_active);
  };

  useEffect(() => {
    authSettingsAdminApi
      .get()
      .then(apply)
      .catch((e) => setMsg({ type: "error", text: e instanceof ApiError ? e.detail : "Ошибка" }))
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const next = await authSettingsAdminApi.update({
        telegram_oidc_enabled: enabled,
        telegram_oidc_client_id: clientId,
        telegram_oidc_client_secret: clientSecret, // "" = не менять
      });
      apply(next);
      setMsg({ type: "success", text: "Сохранено" });
    } catch (e) {
      setMsg({ type: "error", text: e instanceof ApiError ? e.detail : "Ошибка сохранения" });
    } finally {
      setSaving(false);
    }
  };

  const copyRedirect = async () => {
    if (!s?.redirect_uri) return;
    try {
      await navigator.clipboard.writeText(s.redirect_uri);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard может быть недоступен — не критично */
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-border-subtle bg-bg/80 px-5 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <h1 className="flex items-center gap-2 text-xl font-bold text-fg md:text-2xl">
          <KeyRound className="h-5 w-5 text-accent" />
          Вход через Telegram
        </h1>
        <button
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          <Save className="h-4 w-4" />
          {saving ? "Сохранение…" : "Сохранить"}
        </button>
      </div>

      <p className="text-sm text-fg-muted">
        Вход и <span className="text-fg">привязка</span> аккаунта через Telegram работают по
        OpenID Connect. Включите OIDC и вставьте <span className="text-fg">Client ID</span> и{" "}
        <span className="text-fg">Secret</span> из{" "}
        <span className="text-fg">@BotFather → Bot Settings → Web Login</span>. Применяется сразу,
        без переустановки.
      </p>

      <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
            <KeyRound className="h-4 w-4 text-accent" />
            Telegram OIDC
          </h2>
          <span
            className={`text-xs font-medium ${s?.telegram_oidc_active ? "text-success" : "text-fg-subtle"}`}
          >
            {s?.telegram_oidc_active ? "● Активно" : "○ Выключено"}
          </span>
        </div>

        <label className="flex items-center gap-2 text-sm text-fg">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Включить вход и привязку через Telegram (OIDC)
        </label>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-fg">Client ID</label>
          <input
            type="text"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder="напр. 7123456789"
            className="input w-full"
            autoComplete="off"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-fg">Client Secret</label>
          <input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder={
              s?.has_secret ? "•••••• (сохранён) — пусто, чтобы не менять" : "secret из BotFather"
            }
            className="input w-full"
            autoComplete="off"
          />
        </div>

        {s?.redirect_uri && (
          <div>
            <label className="mb-1.5 block text-sm font-medium text-fg">
              Redirect URI (добавить в BotFather → Web Login)
            </label>
            <div className="flex gap-2">
              <input type="text" value={s.redirect_uri} readOnly className="input flex-1 font-mono text-xs" />
              <button
                onClick={copyRedirect}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay"
              >
                {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
                {copied ? "Скопировано" : "Копировать"}
              </button>
            </div>
            <p className="mt-1 text-xs text-fg-subtle">
              Разовый шаг: в @BotFather → Bot Settings → Web Login → Add Redirect URL вставьте этот
              адрес. Без него Telegram не вернёт пользователя после входа.
            </p>
          </div>
        )}

        {msg && (
          <p className={`text-sm ${msg.type === "success" ? "text-success" : "text-danger"}`}>
            {msg.text}
          </p>
        )}
      </section>

      <p className="text-xs text-fg-subtle">
        Если OIDC выключен, кабинет пытается показать классический Login Widget — но он требует
        отдельной привязки домена в @BotFather (<code className="rounded bg-bg-subtle px-1 text-fg">/setdomain</code>),
        что тумблером не настраивается. Рекомендуем OIDC.
      </p>
    </div>
  );
}
