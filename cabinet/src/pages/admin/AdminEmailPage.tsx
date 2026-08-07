import { useEffect, useState } from "react";
import { Save, CheckCircle2, Mail, Send, Server, AlertTriangle } from "lucide-react";
import {
  emailTemplateAdminApi,
  type EmailTemplate,
  type EmailTemplateResponse,
} from "@/api/emailTemplate";
import {
  emailSettingsAdminApi,
  type EmailSettings,
  type EmailSettingsUpdate,
  type EmailProvider,
} from "@/api/emailSettings";
import { ApiError } from "@/types/api";

const PROVIDER_LABELS: Record<EmailProvider, string> = {
  gmail: "Gmail",
  yandex: "Yandex",
  mailru: "Mail.ru",
  brevo: "Brevo (API)",
  custom: "Свой SMTP",
};

// --- договор применимости, общий для обеих карточек экрана --------------------
//
// Экран один, а бэкенды под ним разные. Поверх ЧУЖОГО бота (адаптер,
// adapter/admin_email.py) почта — не наша: письма шлёт он сам, своими ключами и
// своими шаблонами, и наш раздел работает ПУЛЬТОМ к его настройкам. Значит,
// часть элементов формы к нему неприменима, а часть настоящая, но правится не
// отсюда. Бэкенд говорит об этом тремя необязательными картами:
//   supported — элемента у него нет вовсе: не рисуем (иначе админ крутит то,
//               чего не существует, и получает отказ по нажатию);
//   locked    — элемент есть, значение НАСТОЯЩЕЕ, но менять его не отсюда:
//               рисуем выключенным и печатаем причину словами;
//   warnings  — цена рабочего поля: печатаем ВСЕГДА, независимо от locked.
// Наш собственный бэкенд этих полей не присылает — тогда `can` везде true,
// `why` пусто, и экран ведёт себя ровно как раньше.
type Applicability = {
  supported?: Record<string, boolean>;
  locked?: Record<string, string>;
  warnings?: Record<string, string>;
};

function applicability(data: Applicability | null | undefined) {
  const can = (key: string) => data?.supported?.[key] !== false;
  const why = (key: string) => data?.locked?.[key];
  const ro = (key: string) => Boolean(data?.locked?.[key]);
  // Можно ли реально править: элемент есть И не заперт. Ровно по этому признаку
  // поле попадает в тело сохранения — запертое туда класть нельзя, иначе один
  // отказ отменит правки соседних полей (PUT уходит одним запросом).
  const editable = (key: string) => can(key) && !ro(key);
  const warning = (key: string) => data?.warnings?.[key];
  // Причина у запертых полей часто ОБЩАЯ (запрет один на всю карточку) —
  // печатаем её единожды, иначе экран повторяет один абзац восемь раз.
  const printed = new Set<string>();
  const note = (key: string) => {
    const reason = why(key);
    if (!reason || printed.has(reason)) return null;
    printed.add(reason);
    return <p className="mt-1 text-xs leading-snug text-fg-muted">{reason}</p>;
  };
  const warn = (key: string) => {
    const text = warning(key);
    if (!text) return null;
    return (
      <p className="mt-1 flex items-start gap-1.5 text-xs leading-snug text-warning">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{text}</span>
      </p>
    );
  };
  return { can, why, ro, editable, note, warn };
}

// Настройка подключения почты: выбор провайдера (пресет host/port/TLS) + логин/
// пароль/отправитель, либо Brevo по API-ключу. На нашем бэкенде сохраняется в
// assets/email.json и применяется сразу (без рестарта). Пустой пароль/ключ при
// сохранении = «не менять».
function SmtpSettingsCard() {
  const [s, setS] = useState<EmailSettings | null>(null);
  const [provider, setProvider] = useState<EmailProvider>("custom");
  const [host, setHost] = useState("");
  const [port, setPort] = useState(587);
  const [useTls, setUseTls] = useState(true);
  const [useSsl, setUseSsl] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fromEmail, setFromEmail] = useState("");
  const [fromName, setFromName] = useState("");
  const [brevoKey, setBrevoKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testTo, setTestTo] = useState("");
  const [sending, setSending] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const apply = (d: EmailSettings) => {
    setS(d);
    setProvider(d.provider);
    setHost(d.host);
    setPort(d.port);
    setUseTls(d.use_tls);
    setUseSsl(d.use_ssl);
    setUsername(d.username);
    setFromEmail(d.from_email);
    setFromName(d.from_name);
    setPassword("");
    setBrevoKey("");
  };

  useEffect(() => {
    emailSettingsAdminApi
      .get()
      .then(apply)
      .catch((e) => setMsg({ type: "error", text: e instanceof ApiError ? e.detail : "Ошибка" }))
      .finally(() => setLoading(false));
  }, []);

  const { can, ro, editable, note, warn } = applicability(s);

  const onProvider = (p: EmailProvider) => {
    setProvider(p);
    const preset = s?.presets?.[p];
    if (preset) {
      setHost(preset.host);
      setPort(preset.port);
      setUseTls(preset.use_tls);
      setUseSsl(preset.use_ssl);
    }
  };

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      // Кладём только то, что этот бэкенд умеет и разрешает менять. Наш
      // собственный про применимость не рассказывает — там в тело попадает всё,
      // ровно как раньше.
      const body: EmailSettingsUpdate = {};
      if (can("enabled")) body.enabled = true;
      if (editable("provider")) body.provider = provider;
      if (editable("host")) body.host = host;
      if (editable("port")) body.port = port;
      if (editable("use_tls")) body.use_tls = useTls;
      if (editable("use_ssl")) body.use_ssl = useSsl;
      if (editable("username")) body.username = username;
      if (editable("from_email")) body.from_email = fromEmail;
      if (editable("from_name")) body.from_name = fromName;
      if (editable("password")) body.password = password; // "" = не менять
      if (editable("brevo_api_key")) body.brevo_api_key = brevoKey; // "" = не менять
      const next = await emailSettingsAdminApi.update(body);
      apply(next);
      setMsg({ type: "success", text: "Сохранено" });
    } catch (e) {
      setMsg({ type: "error", text: e instanceof ApiError ? e.detail : "Ошибка сохранения" });
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async () => {
    setSending(true);
    setMsg(null);
    try {
      await emailSettingsAdminApi.sendTest(testTo);
      setMsg({ type: "success", text: `Тестовое письмо отправлено на ${testTo}` });
    } catch (e) {
      setMsg({ type: "error", text: e instanceof ApiError ? e.detail : "Не удалось отправить" });
    } finally {
      setSending(false);
    }
  };

  if (loading) return null;

  const isBrevo = provider === "brevo";
  const isPreset = provider === "gmail" || provider === "yandex" || provider === "mailru";
  // Список провайдеров: Brevo убираем там, где отправки через его API нет вовсе
  // (у чужого бота бывает только SMTP). Текущее значение оставляем всегда —
  // иначе выпадающий список показал бы не то, что настроено.
  const providers = (Object.keys(PROVIDER_LABELS) as EmailProvider[]).filter(
    (p) => p === provider || p !== "brevo" || can("brevo_api_key"),
  );
  // Менять нечего — нечего и сохранять: кнопка отправила бы пустое тело и
  // ответила «Сохранено», ничего не изменив.
  const anyEditable = [
    "provider",
    "host",
    "port",
    "use_tls",
    "use_ssl",
    "username",
    "password",
    "from_email",
    "from_name",
    "brevo_api_key",
  ].some(editable);

  return (
    <section className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
          <Server className="h-4 w-4 text-accent" />
          Подключение почты (SMTP)
        </h2>
        <span
          className={`text-xs font-medium ${s?.is_enabled ? "text-success" : "text-fg-subtle"}`}
        >
          {s?.is_enabled ? "● Готово к отправке" : "○ Не настроено"}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {can("provider") && (
          <div>
            <label className="mb-1.5 block text-sm font-medium text-fg">Провайдер</label>
            <select
              value={provider}
              disabled={ro("provider")}
              onChange={(e) => onProvider(e.target.value as EmailProvider)}
              className={`input w-full ${ro("provider") ? "cursor-not-allowed opacity-60" : ""}`}
            >
              {providers.map((p) => (
                <option key={p} value={p}>
                  {PROVIDER_LABELS[p]}
                </option>
              ))}
            </select>
            {note("provider")}
            {warn("provider")}
          </div>
        )}
        {can("from_email") && (
          <div>
            <label className="mb-1.5 block text-sm font-medium text-fg">Отправитель (From)</label>
            <input
              type="email"
              value={fromEmail}
              disabled={ro("from_email")}
              onChange={(e) => setFromEmail(e.target.value)}
              placeholder="noreply@example.com"
              className={`input w-full ${ro("from_email") ? "cursor-not-allowed opacity-60" : ""}`}
            />
            {note("from_email")}
            {warn("from_email")}
          </div>
        )}
      </div>

      {can("from_name") && (
        <div>
          <label className="mb-1.5 block text-sm font-medium text-fg">Имя отправителя</label>
          <input
            type="text"
            value={fromName}
            disabled={ro("from_name")}
            onChange={(e) => setFromName(e.target.value)}
            placeholder="My VPN"
            className={`input w-full ${ro("from_name") ? "cursor-not-allowed opacity-60" : ""}`}
          />
          {note("from_name")}
          {warn("from_name")}
        </div>
      )}

      {isBrevo ? (
        <div>
          <label className="mb-1.5 block text-sm font-medium text-fg">Brevo API key</label>
          <input
            type="password"
            value={brevoKey}
            disabled={ro("brevo_api_key")}
            onChange={(e) => setBrevoKey(e.target.value)}
            placeholder={s?.has_brevo_key ? "•••••• (сохранён) — оставьте пустым, чтобы не менять" : "xkeysib-…"}
            className={`input w-full ${ro("brevo_api_key") ? "cursor-not-allowed opacity-60" : ""}`}
            autoComplete="off"
          />
          {note("brevo_api_key")}
          {warn("brevo_api_key")}
        </div>
      ) : (
        <>
          {provider === "custom" && (
            <div className="grid gap-3 sm:grid-cols-3">
              {can("host") && (
                <div className="sm:col-span-2">
                  <label className="mb-1.5 block text-sm font-medium text-fg">SMTP host</label>
                  <input type="text" value={host} disabled={ro("host")}
                    onChange={(e) => setHost(e.target.value)}
                    placeholder="smtp.example.com"
                    className={`input w-full ${ro("host") ? "cursor-not-allowed opacity-60" : ""}`} />
                  {note("host")}
                  {warn("host")}
                </div>
              )}
              {can("port") && (
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-fg">Port</label>
                  <input type="number" value={port} disabled={ro("port")}
                    onChange={(e) => setPort(Number(e.target.value))}
                    className={`input w-full ${ro("port") ? "cursor-not-allowed opacity-60" : ""}`} />
                  {note("port")}
                  {warn("port")}
                </div>
              )}
              {can("use_tls") && (
                <div>
                  <label
                    className={`flex items-center gap-2 text-sm text-fg ${
                      ro("use_tls") ? "cursor-not-allowed opacity-60" : ""
                    }`}
                  >
                    <input type="checkbox" checked={useTls} disabled={ro("use_tls")}
                      onChange={(e) => setUseTls(e.target.checked)} />
                    STARTTLS
                  </label>
                  {note("use_tls")}
                  {warn("use_tls")}
                </div>
              )}
              {can("use_ssl") && (
                <div>
                  <label
                    className={`flex items-center gap-2 text-sm text-fg ${
                      ro("use_ssl") ? "cursor-not-allowed opacity-60" : ""
                    }`}
                  >
                    <input type="checkbox" checked={useSsl} disabled={ro("use_ssl")}
                      onChange={(e) => setUseSsl(e.target.checked)} />
                    SSL
                  </label>
                  {note("use_ssl")}
                  {warn("use_ssl")}
                </div>
              )}
            </div>
          )}
          {isPreset && (
            <div>
              <p className="text-xs text-fg-subtle">
                Сервер: <span className="text-fg">{host}:{port}</span> ({useSsl ? "SSL" : useTls ? "STARTTLS" : "без шифрования"}) — задаётся автоматически.
                Пароль — это <span className="text-fg">пароль приложения</span> (app password), а не пароль от аккаунта.
              </p>
              {/* Про сервер и шифрование бэкенд тоже может иметь что сказать —
                  например, что на порту 465 он включает SSL сам. Поля скрыты
                  пресетом, но предупреждение относится к тому, что показано выше. */}
              {warn("use_ssl")}
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            {can("username") && (
              <div>
                <label className="mb-1.5 block text-sm font-medium text-fg">Логин (email)</label>
                <input type="text" value={username} disabled={ro("username")}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="you@gmail.com" autoComplete="off"
                  className={`input w-full ${ro("username") ? "cursor-not-allowed opacity-60" : ""}`} />
                {note("username")}
                {warn("username")}
              </div>
            )}
            {can("password") && (
              <div>
                <label className="mb-1.5 block text-sm font-medium text-fg">Пароль</label>
                <input type="password" value={password} disabled={ro("password")}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={s?.has_password ? "•••••• (сохранён) — пусто, чтобы не менять" : "app password"}
                  autoComplete="off"
                  className={`input w-full ${ro("password") ? "cursor-not-allowed opacity-60" : ""}`} />
                {note("password")}
                {/* Предупреждение печатается независимо от запрета: поверх чужого
                    бота вписанный пароль ложится в его журнал открытым текстом, и
                    узнать об этом человек должен ДО сохранения. */}
                {warn("password")}
              </div>
            )}
          </div>
        </>
      )}

      <div className="flex flex-wrap items-center gap-2">
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
        {can("test") && (
          <>
            <input
              type="email"
              placeholder="test@example.com"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              className="input flex-1 min-w-[180px]"
            />
            <button
              onClick={sendTest}
              disabled={sending || !testTo}
              className="inline-flex items-center gap-2 rounded-xl border border-border-subtle bg-bg px-4 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay disabled:opacity-50"
            >
              <Send className="h-4 w-4" />
              {sending ? "Отправка…" : "Тест"}
            </button>
          </>
        )}
      </div>

      {msg && (
        <p className={`text-sm ${msg.type === "success" ? "text-success" : "text-danger"}`}>
          {msg.text}
        </p>
      )}
    </section>
  );
}

type Key = keyof EmailTemplate;

const FIELDS: { key: Key; label: string; hint?: string; multiline?: boolean }[] = [
  { key: "subject", label: "Тема письма", hint: "Например: Код подтверждения — {brand}" },
  { key: "heading", label: "Заголовок в письме" },
  { key: "intro", label: "Текст перед кодом", multiline: true },
  { key: "expire_note", label: "Про срок действия", hint: "Можно использовать {minutes}" },
  { key: "ignore_note", label: "Примечание внизу", multiline: true },
];

export default function AdminEmailPage() {
  const [form, setForm] = useState<EmailTemplateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [testTo, setTestTo] = useState("");
  const [sending, setSending] = useState(false);
  const [testMsg, setTestMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    emailTemplateAdminApi
      .get()
      .then(setForm)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка загрузки"))
      .finally(() => setLoading(false));
  }, []);

  const { can, editable, ro, note, warn } = applicability(form);
  // Поля, которые этот бэкенд вообще показывает, и те, что даёт править.
  const shown = FIELDS.filter((f) => can(f.key));
  const anyEditable = FIELDS.some((f) => editable(f.key));

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      // Только правимые поля: запертые бэкенд всё равно отвергнет, а вместе с
      // ними отменит и соседние — сохранение уходит одним запросом.
      const body: Partial<EmailTemplate> = {};
      for (const f of FIELDS) if (editable(f.key)) body[f.key] = form[f.key];
      const next = await emailTemplateAdminApi.update(body);
      setForm(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async () => {
    setSending(true);
    setTestMsg(null);
    try {
      await emailTemplateAdminApi.sendTest(testTo);
      setTestMsg({ type: "success", text: `Тестовое письмо отправлено на ${testTo}` });
    } catch (e) {
      setTestMsg({
        type: "error",
        text: e instanceof ApiError ? e.detail : "Не удалось отправить",
      });
    } finally {
      setSending(false);
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  if (!form) return null;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-border-subtle bg-bg/80 px-5 py-3 backdrop-blur-md md:-mx-8 md:px-8">
        <h1 className="flex items-center gap-2 text-xl font-bold text-fg md:text-2xl">
          <Mail className="h-5 w-5 text-accent" />
          Письмо с кодом
        </h1>
        {anyEditable && (
          <button
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
            {saved ? "Сохранено" : saving ? "Сохранение…" : "Сохранить"}
          </button>
        )}
      </div>

      <SmtpSettingsCard />

      {anyEditable ? (
        <p className="text-sm text-fg-muted">
          Текст письма с кодом подтверждения. Доступные подстановки:{" "}
          <code className="rounded bg-bg-subtle px-1 text-fg">{"{brand}"}</code> (имя из
          EMAIL_FROM_NAME),{" "}
          <code className="rounded bg-bg-subtle px-1 text-fg">{"{code}"}</code>,{" "}
          <code className="rounded bg-bg-subtle px-1 text-fg">{"{minutes}"}</code>. Применяется
          сразу после «Сохранить». Пустое поле вернётся к стандартному тексту.
        </p>
      ) : (
        <p className="text-sm text-fg-muted">
          Тексты писем на этом бэкенде правятся не здесь — причина под полем ниже. Показано то,
          что реально уходит людям.
        </p>
      )}

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="space-y-4">
        {shown.map((f) => (
          <div key={f.key}>
            <label className="mb-1.5 block text-sm font-medium text-fg">{f.label}</label>
            {f.multiline ? (
              <textarea
                rows={2}
                value={form[f.key]}
                disabled={ro(f.key)}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                className={`input w-full ${ro(f.key) ? "cursor-not-allowed opacity-60" : ""}`}
              />
            ) : (
              <input
                type="text"
                value={form[f.key]}
                disabled={ro(f.key)}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                className={`input w-full ${ro(f.key) ? "cursor-not-allowed opacity-60" : ""}`}
              />
            )}
            {/* Подсказка про подстановки — только пока поле правится: в чужом
                письме наших {brand}/{minutes} нет, и звать их вписывать нельзя. */}
            {f.hint && editable(f.key) && (
              <p className="mt-1 text-xs text-fg-subtle">{f.hint}</p>
            )}
            {note(f.key)}
            {warn(f.key)}
          </div>
        ))}
      </div>

      {/* Тест-отправка */}
      {can("test") && (
        <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
          <h2 className="text-sm font-semibold text-fg">Проверить отправку</h2>
          <p className="mt-0.5 text-xs text-fg-muted">
            {anyEditable ? (
              <>
                Отправит тестовое письмо с кодом <span className="text-fg">123456</span> на
                указанный адрес (текущим сохранённым шаблоном).
              </>
            ) : (
              <>
                Отправит на указанный адрес настоящее письмо — тем шаблоном, которым бэкенд шлёт
                его людям. Проверяется именно отправка: дошло или нет.
              </>
            )}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <input
              type="email"
              placeholder="you@example.com"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              className="input flex-1"
            />
            <button
              onClick={sendTest}
              disabled={sending || !testTo}
              className="inline-flex items-center gap-2 rounded-xl border border-border-subtle bg-bg px-4 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay disabled:opacity-50"
            >
              <Send className="h-4 w-4" />
              {sending ? "Отправка…" : "Отправить тест"}
            </button>
          </div>
          {testMsg && (
            <p className={`mt-2 text-sm ${testMsg.type === "success" ? "text-success" : "text-danger"}`}>
              {testMsg.text}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
