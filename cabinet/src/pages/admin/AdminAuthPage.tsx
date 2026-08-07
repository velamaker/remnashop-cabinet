import { useEffect, useState } from "react";
import { Save, KeyRound, Copy, Check, AlertTriangle } from "lucide-react";
import { authSettingsAdminApi, type AuthSettings } from "@/api/authSettings";
import { ApiError } from "@/types/api";

// Включение входа/привязки через Telegram (OIDC) прямо из админки: Client ID и
// Secret из @BotFather → Web Login сохраняются в assets/auth.json и применяются
// сразу, без переустановки. Пустой Secret при сохранении = «не менять».
//
// Поверх ЧУЖОГО бота (адаптер, adapter/admin_auth.py) часть полей может быть
// неприменима: их нет вовсе (`supported`) или они есть, но правятся не отсюда
// (`locked` — причина текстом). Экран обязан это читать: иначе он рисует галку,
// которая никогда не сработает, и подсказывает ровно тот путь, который бэкенд
// отвергнет. Наш собственный бэкенд этих полей не присылает — тогда всё как
// раньше. Запертые поля в сохранение не кладём: PUT уходит одним запросом, и
// отказ по одному полю отменил бы заодно правки соседних.
//
// Отдельно от запретов приходит `warnings` — цена рабочего поля. Поверх «Бедолаги»
// сохранённый Client Secret она кладёт в свой журнал действий открытым текстом
// (её дефект, адаптером не чинится), поэтому подпись у поля печатается ВСЕГДА,
// а не только пока поле заперто.
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

  // Показывать поле? Признака нет — да (наш бэкенд про `supported` не знает).
  const can = (key: string) => s?.supported?.[key] !== false;
  // Правится не отсюда — причина текстом.
  const why = (key: string) => s?.locked?.[key];
  // Рисуем, но выключенным: значение настоящее, менять его нельзя.
  const ro = (key: string) => Boolean(why(key));
  // Включить OIDC отсюда нельзя вовсе: бэкенд запер оба слагаемых своей формулы
  // (тумблер и Client ID). Тогда и вводные тексты не должны звать этим путём.
  const cantEnable =
    !s?.telegram_oidc_active && (ro("telegram_oidc_enabled") || ro("telegram_oidc_client_id"));
  // Можно ли вообще вписать Secret. Поверх «Бедолаги» он неприменим (её вход
  // проверяет id_token подписью), поэтому вводный текст не должен звать его
  // вставлять: поле рядом всё равно выключено.
  const secretUsable = can("telegram_oidc_client_secret") && !ro("telegram_oidc_client_secret");
  const FIELDS = [
    "telegram_oidc_enabled",
    "telegram_oidc_client_id",
    "telegram_oidc_client_secret",
  ];
  // Нечего править — нечего и сохранять: кнопка отправляла бы пустой запрос и
  // отвечала «Сохранено», ничего не изменив.
  const anyEditable = FIELDS.some((k) => can(k) && !ro(k));
  // Причина у запертых полей часто одна и та же (запрет общий, а не по полю) —
  // печатаем её один раз, иначе экран трижды повторяет один абзац.
  const printed = new Set<string>();
  const note = (key: string) => {
    const reason = why(key);
    if (!reason || printed.has(reason)) return null;
    printed.add(reason);
    return <p className="mt-1 text-xs leading-snug text-fg-muted">{reason}</p>;
  };
  // Предупреждение о цене поля (не запрет!) — печатаем всегда, пока бэкенд его
  // шлёт: поверх чужого бота сохранённый Client Secret ложится в его журнал
  // действий открытым текстом, и узнать об этом человек должен ДО сохранения, а
  // не когда поле разблокируется. Наш бэкенд `warnings` не присылает.
  const warn = (key: string) => {
    const text = s?.warnings?.[key];
    if (!text) return null;
    return (
      <p className="mt-1 flex items-start gap-1.5 text-xs leading-snug text-warning">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{text}</span>
      </p>
    );
  };

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const body: Parameters<typeof authSettingsAdminApi.update>[0] = {};
      if (can("telegram_oidc_enabled") && !ro("telegram_oidc_enabled"))
        body.telegram_oidc_enabled = enabled;
      if (can("telegram_oidc_client_id") && !ro("telegram_oidc_client_id"))
        body.telegram_oidc_client_id = clientId;
      if (can("telegram_oidc_client_secret") && !ro("telegram_oidc_client_secret"))
        body.telegram_oidc_client_secret = clientSecret; // "" = не менять
      const next = await authSettingsAdminApi.update(body);
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
        {anyEditable && (
          <button
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            <Save className="h-4 w-4" />
            {saving ? "Сохранение…" : "Сохранить"}
          </button>
        )}
      </div>

      {cantEnable ? (
        <p className="text-sm text-fg-muted">
          На этом бэкенде вход через Telegram работает{" "}
          <span className="text-fg">классическим Login Widget</span> — он включён и настраивать
          его здесь нечего. Вход по <span className="text-fg">OpenID Connect</span> недоступен,
          причина — под полями.
        </p>
      ) : (
        <p className="text-sm text-fg-muted">
          Вход и <span className="text-fg">привязка</span> аккаунта через Telegram работают по
          OpenID Connect. Включите OIDC и вставьте <span className="text-fg">Client ID</span>
          {secretUsable ? (
            <>
              {" "}
              и <span className="text-fg">Secret</span>
            </>
          ) : null}{" "}
          из <span className="text-fg">@BotFather → Bot Settings → Web Login</span>. Применяется
          сразу, без переустановки.
        </p>
      )}

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

        {can("telegram_oidc_enabled") && (
          <div>
            <label
              className={`flex items-center gap-2 text-sm text-fg ${
                ro("telegram_oidc_enabled") ? "cursor-not-allowed opacity-60" : ""
              }`}
            >
              <input
                type="checkbox"
                checked={enabled}
                disabled={ro("telegram_oidc_enabled")}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              Включить вход и привязку через Telegram (OIDC)
            </label>
            {note("telegram_oidc_enabled")}
          </div>
        )}

        {can("telegram_oidc_client_id") && (
          <div>
            <label className="mb-1.5 block text-sm font-medium text-fg">Client ID</label>
            <input
              type="text"
              value={clientId}
              disabled={ro("telegram_oidc_client_id")}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="напр. 7123456789"
              className={`input w-full ${
                ro("telegram_oidc_client_id") ? "cursor-not-allowed opacity-60" : ""
              }`}
              autoComplete="off"
            />
            {note("telegram_oidc_client_id")}
          </div>
        )}

        {can("telegram_oidc_client_secret") && (
          <div>
            <label className="mb-1.5 block text-sm font-medium text-fg">Client Secret</label>
            <input
              type="password"
              value={clientSecret}
              disabled={ro("telegram_oidc_client_secret")}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder={
                s?.has_secret ? "•••••• (сохранён) — пусто, чтобы не менять" : "secret из BotFather"
              }
              className={`input w-full ${
                ro("telegram_oidc_client_secret") ? "cursor-not-allowed opacity-60" : ""
              }`}
              autoComplete="off"
            />
            {note("telegram_oidc_client_secret")}
            {warn("telegram_oidc_client_secret")}
          </div>
        )}

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

      {cantEnable ? (
        <p className="text-xs text-fg-subtle">
          Классический Login Widget требует разовой привязки домена кабинета в @BotFather
          (<code className="rounded bg-bg-subtle px-1 text-fg">/setdomain</code>) — это делается в
          самом Telegram, тумблером не настраивается. Без неё кнопка Telegram на странице входа
          скажет «Bot domain invalid».
        </p>
      ) : (
        <p className="text-xs text-fg-subtle">
          Если OIDC выключен, кабинет пытается показать классический Login Widget — но он требует
          отдельной привязки домена в @BotFather (<code className="rounded bg-bg-subtle px-1 text-fg">/setdomain</code>),
          что тумблером не настраивается. Рекомендуем OIDC.
        </p>
      )}
    </div>
  );
}
