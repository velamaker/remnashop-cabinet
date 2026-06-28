import { useEffect, useState } from "react";
import { Save, CheckCircle2, Mail, Send } from "lucide-react";
import { emailTemplateAdminApi, type EmailTemplate } from "@/api/emailTemplate";
import { ApiError } from "@/types/api";

type Key = keyof EmailTemplate;

const FIELDS: { key: Key; label: string; hint?: string; multiline?: boolean }[] = [
  { key: "subject", label: "Тема письма", hint: "Например: Код подтверждения — {brand}" },
  { key: "heading", label: "Заголовок в письме" },
  { key: "intro", label: "Текст перед кодом", multiline: true },
  { key: "expire_note", label: "Про срок действия", hint: "Можно использовать {minutes}" },
  { key: "ignore_note", label: "Примечание внизу", multiline: true },
];

export default function AdminEmailPage() {
  const [form, setForm] = useState<EmailTemplate | null>(null);
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

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      const next = await emailTemplateAdminApi.update(form);
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
        <button
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved ? "Сохранено" : saving ? "Сохранение…" : "Сохранить"}
        </button>
      </div>

      <p className="text-sm text-fg-muted">
        Текст письма с кодом подтверждения. Доступные подстановки:{" "}
        <code className="rounded bg-bg-subtle px-1 text-fg">{"{brand}"}</code> (имя из
        EMAIL_FROM_NAME),{" "}
        <code className="rounded bg-bg-subtle px-1 text-fg">{"{code}"}</code>,{" "}
        <code className="rounded bg-bg-subtle px-1 text-fg">{"{minutes}"}</code>. Применяется
        сразу после «Сохранить». Пустое поле вернётся к стандартному тексту.
      </p>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="space-y-4">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label className="mb-1.5 block text-sm font-medium text-fg">{f.label}</label>
            {f.multiline ? (
              <textarea
                rows={2}
                value={form[f.key]}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                className="input w-full"
              />
            ) : (
              <input
                type="text"
                value={form[f.key]}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                className="input w-full"
              />
            )}
            {f.hint && <p className="mt-1 text-xs text-fg-subtle">{f.hint}</p>}
          </div>
        ))}
      </div>

      {/* Тест-отправка */}
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h2 className="text-sm font-semibold text-fg">Проверить отправку</h2>
        <p className="mt-0.5 text-xs text-fg-muted">
          Отправит тестовое письмо с кодом <span className="text-fg">123456</span> на
          указанный адрес (текущим сохранённым шаблоном).
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
    </div>
  );
}
