import { useEffect, useState } from "react";
import { ShieldCheck, Loader2, Copy, Check, KeyRound } from "lucide-react";
import { twoFactorApi, type TwoFactorSetup } from "@/api/admin";
import { ApiError } from "@/types/api";

/** Модалка разблокировки 2FA — всплывает, когда админ-запрос вернул «2fa_required». */
export function Admin2FAUnlock() {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const on = () => setOpen(true);
    window.addEventListener("admin-2fa-required", on);
    return () => window.removeEventListener("admin-2fa-required", on);
  }, []);

  if (!open) return null;

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await twoFactorApi.unlock(code.trim());
      window.location.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Ошибка");
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-bg p-5">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-accent" />
          <h2 className="text-base font-bold text-fg">Подтверждение 2FA</h2>
        </div>
        <p className="mt-1 text-xs text-fg-muted">Введите 6-значный код из приложения-аутентификатора.</p>
        <input
          autoFocus
          inputMode="numeric"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
          onKeyDown={(e) => e.key === "Enter" && code.length === 6 && submit()}
          placeholder="000000"
          className="mt-3 w-full rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2.5 text-center text-lg tracking-widest text-fg focus:outline-none focus:ring-2 focus:ring-accent"
        />
        {err && <p className="mt-2 text-xs text-danger">{err}</p>}
        <button
          onClick={submit}
          disabled={busy || code.length !== 6}
          className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg hover:bg-accent/90 disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
          Разблокировать
        </button>
      </div>
    </div>
  );
}

/** Карточка управления 2FA: включение (секрет+код) / выключение. */
export function TwoFactorCard() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    twoFactorApi.status().then((s) => setEnabled(s.enabled)).catch(() => setEnabled(null));
  }, []);

  if (enabled === null) return null;

  const startSetup = async () => {
    setBusy(true);
    setErr(null);
    try {
      setSetup(await twoFactorApi.setup());
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setBusy(false);
    }
  };

  const confirmEnable = async () => {
    setBusy(true);
    setErr(null);
    try {
      await twoFactorApi.enable(code.trim());
      setEnabled(true);
      setSetup(null);
      setCode("");
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Неверный код");
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    const c = prompt("Введите код 2FA для выключения:");
    if (!c) return;
    setBusy(true);
    setErr(null);
    try {
      await twoFactorApi.disable(c.trim());
      setEnabled(false);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setBusy(false);
    }
  };

  const copySecret = async () => {
    if (!setup) return;
    try {
      await navigator.clipboard.writeText(setup.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-accent" />
        <h3 className="text-base font-bold text-fg">Двухфакторная защита (2FA)</h3>
      </div>
      <p className="mt-1 text-xs text-fg-muted">
        TOTP для вашего админ-аккаунта (Google Authenticator / любое приложение). Включается лично для вас.
      </p>

      {enabled ? (
        <div className="mt-4">
          <p className="flex items-center gap-1.5 text-sm text-success">
            <Check className="h-4 w-4" /> 2FA включена
          </p>
          <button onClick={disable} disabled={busy} className="mt-3 rounded-xl border border-danger/40 bg-danger/10 px-4 py-2 text-sm font-medium text-danger hover:bg-danger/15 disabled:opacity-50">
            Выключить 2FA
          </button>
        </div>
      ) : setup ? (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-fg-muted">1. Добавьте в приложение-аутентификатор секрет:</p>
          <div className="flex items-center gap-2">
            <code className="rounded-lg bg-bg px-3 py-1.5 text-sm font-bold tracking-wider text-fg break-all">{setup.secret}</code>
            <button onClick={copySecret} className="inline-flex items-center gap-1 rounded-lg border border-border-subtle bg-bg px-2.5 py-1.5 text-xs text-fg hover:bg-bg-raised">
              {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          </div>
          <p className="text-xs text-fg-subtle break-all">Или ссылка: <a href={setup.otpauth} className="text-accent">{setup.otpauth}</a></p>
          <p className="text-sm text-fg-muted">2. Введите код из приложения:</p>
          <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="000000" className={inputCls} />
          {err && <p className="text-xs text-danger">{err}</p>}
          <button onClick={confirmEnable} disabled={busy || code.length !== 6} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />} Включить 2FA
          </button>
        </div>
      ) : (
        <div className="mt-4">
          {err && <p className="mb-2 text-xs text-danger">{err}</p>}
          <button onClick={startSetup} disabled={busy} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />} Включить 2FA
          </button>
        </div>
      )}
    </section>
  );
}
