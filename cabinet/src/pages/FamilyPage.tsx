import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Check, Copy, QrCode, RotateCcw, Smartphone, Trash2, UsersRound, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { familyApi, type FamilyProfile, type FamilyResponse } from "@/api/family";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { useBranding } from "@/contexts/BrandingContext";
import { resetFamilyNav } from "@/hooks/useFamilyNav";
import { useT } from "@/i18n/I18nContext";
import { newRequestId } from "@/lib/bulkJobs";
import { formatBytes, formatDate, formatDateTime } from "@/lib/format";
import { ApiError } from "@/types/api";

/**
 * «Семья»: профили для близких к семейному тарифу владельца.
 *
 * У каждого профиля своя ссылка подписки, свои устройства и весь трафик тарифа;
 * участнику аккаунт не нужен — владелец пересылает ему ссылку (копировать или QR).
 * Лимит устройств самого владельца профили не трогают.
 *
 * Имя профиля пишет человек — выводим его только текстом (React экранирует сам).
 * `request_id` живёт, пока попытка не получила окончательного ответа: повтор после
 * обрыва связи — тот же ключ, и бот не заведёт второй профиль.
 */

const REASONS = new Set([
  "no_subscription",
  "blocked",
  "trial",
  "not_family",
  "frozen",
  "reserve",
  "not_active",
  "max_reached",
]);
const SUSPEND_REASONS = new Set(["plan", "owner_expired", "owner_frozen", "owner_gone", "owner_blocked"]);

function useFamily(allowed: boolean) {
  const [data, setData] = useState<FamilyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(allowed);

  const load = useCallback(async () => {
    // Бот не умеет семью — не ходим за ручкой, которой у него нет.
    if (!allowed) return;
    try {
      setData(await familyApi.get());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "");
    } finally {
      setLoading(false);
    }
  }, [allowed]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, error, loading, reload: load };
}

function LinkBox({ url }: { url: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [showQr, setShowQr] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* буфер недоступен (незащищённый контекст) — ссылка видна текстом */
    }
  };

  return (
    <div className="mt-3">
      <p className="text-xs text-fg-subtle">{t("family.linkHint")}</p>
      <div className="mt-1.5 flex items-center gap-2 rounded-xl bg-bg-subtle p-2">
        <code className="flex-1 truncate px-2 text-xs text-fg-muted">{url}</code>
        <Button size="sm" variant="secondary" onClick={copy}>
          {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
          {copied ? t("connect.copied") : t("connect.copy")}
        </Button>
      </div>
      <button
        type="button"
        onClick={() => setShowQr((v) => !v)}
        className="mt-2 inline-flex items-center gap-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg"
      >
        {showQr ? <X className="h-4 w-4" /> : <QrCode className="h-4 w-4" />}
        {showQr ? t("family.hideQr") : t("family.showQr")}
      </button>
      {showQr && (
        <div className="mt-3 flex justify-center">
          <div className="rounded-2xl bg-white p-3">
            <QRCodeSVG value={url} size={180} />
          </div>
        </div>
      )}
    </div>
  );
}

function statusText(t: ReturnType<typeof useT>, p: FamilyProfile): string {
  if (p.status === "creating") return t("family.status.creating");
  if (p.status === "deleting") return t("family.status.deleting");
  if (p.status === "suspended") {
    const reason = p.suspend_reason && SUSPEND_REASONS.has(p.suspend_reason) ? p.suspend_reason : "plan";
    return t("family.suspended", { reason: t(`family.suspend.${reason}`) });
  }
  if (p.expired) return t("family.status.expired");
  return t("family.status.active");
}

function ProfileCard({
  profile,
  resetEnabled,
  onChanged,
}: {
  profile: FamilyProfile;
  resetEnabled: boolean;
  onChanged: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState<"reset" | "delete" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = profile.status === "active";

  const reset = async () => {
    setBusy("reset");
    setNote(null);
    setError(null);
    try {
      const res = await familyApi.resetDevices(profile.id);
      if (res.result === "reset") setNote(t("family.resetDone"));
      else if (res.result === "cooldown")
        setError(t("family.resetCooldown", { date: res.available_at ? formatDateTime(res.available_at) : "—" }));
      else setError(res.reason === "disabled" ? t("family.resetDisabled") : t("family.errGeneric"));
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("family.errGeneric"));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy("delete");
    setError(null);
    try {
      const res = await familyApi.remove(profile.id);
      setNote(res.result === "deleted" ? t("family.deleted") : t("family.deletePending"));
      setConfirming(false);
      resetFamilyNav();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("family.errGeneric"));
    } finally {
      setBusy(null);
    }
  };

  const traffic =
    profile.traffic_limit_bytes === 0
      ? t("family.trafficUnlimited")
      : profile.traffic_limit_bytes != null
        ? t("family.traffic", {
            used: profile.traffic_used_bytes != null ? formatBytes(profile.traffic_used_bytes) : "—",
            limit: formatBytes(profile.traffic_limit_bytes),
          })
        : null;

  return (
    <div className="surface rounded-xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-fg">{profile.label}</p>
          <p className={`mt-0.5 text-xs ${active && !profile.expired ? "text-success" : "text-fg-muted"}`}>
            {statusText(t, profile)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 text-xs text-fg-muted">
          <Smartphone className="h-4 w-4" />
          <span className="tabular">
            {t("family.devices", { count: profile.devices ?? "—", limit: profile.device_limit })}
          </span>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-subtle">
        {traffic && <span>{traffic}</span>}
        {profile.expire_at && <span>{t("family.until", { date: formatDate(profile.expire_at) })}</span>}
      </div>

      {active && profile.url && <LinkBox url={profile.url} />}

      {note && <p className="mt-3 text-xs text-fg">{note}</p>}
      {error && <p className="mt-3 text-xs text-danger">{error}</p>}

      {profile.status !== "deleting" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {active && resetEnabled && (
            <Button size="sm" variant="secondary" onClick={reset} isLoading={busy === "reset"} disabled={busy != null}>
              <RotateCcw className="h-3.5 w-3.5" />
              {t("family.resetDevices")}
            </Button>
          )}
          {!confirming ? (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(true)} disabled={busy != null}>
              <Trash2 className="h-3.5 w-3.5" />
              {t("family.delete")}
            </Button>
          ) : (
            <div className="flex w-full flex-col gap-2 rounded-xl border border-danger/30 p-3">
              <p className="text-xs text-fg">{t("family.deleteConfirm", { name: profile.label })}</p>
              <div className="flex gap-2">
                <Button size="sm" variant="danger" onClick={remove} isLoading={busy === "delete"}>
                  {t("family.deleteYes")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={busy != null}>
                  {t("family.cancel")}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AddProfile({ onCreated }: { onCreated: () => void }) {
  const t = useT();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Ключ попытки: повтор после обрыва связи — тот же профиль, а не второй.
  const requestId = useRef<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const label = name.trim();
    if (!label) {
      setError(t("family.errBadLabel"));
      return;
    }
    requestId.current ??= newRequestId();
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const res = await familyApi.create({ request_id: requestId.current, label });
      // Окончательный ответ — следующей попытке нужен новый ключ.
      requestId.current = null;
      if (res.result === "created") {
        setNote(t("family.created", { name: label }));
        setName("");
        resetFamilyNav();
        onCreated();
      } else if (res.result === "pending") {
        setNote(t("family.pending"));
        onCreated();
      } else if (res.result === "label_taken") {
        setError(t("family.errLabelTaken"));
      } else if (res.result === "bad_label") {
        setError(t("family.errBadLabel"));
      } else if (res.result === "not_available") {
        const reason = REASONS.has(res.reason) ? res.reason : "not_active";
        setError(t("family.cantAdd", { reason: t(`family.reason.${reason}`) }));
        onCreated();
      } else {
        setError(t("family.errGeneric"));
      }
    } catch (err) {
      // 502 — панель отказала: ключ «сгорел», следующая попытка — с новым.
      if (err instanceof ApiError && err.status === 502) requestId.current = null;
      setError(err instanceof ApiError && err.status === 502 ? t("family.errFailed") : t("family.errGeneric"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={24}
          placeholder={t("family.namePlaceholder")}
          aria-label={t("family.namePlaceholder")}
          className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
        />
        {note && <p className="mt-1.5 text-xs text-fg">{note}</p>}
        {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
      </div>
      <button
        type="submit"
        disabled={busy}
        className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl btn-gradient border-0 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        {t("family.add")}
      </button>
    </form>
  );
}

export default function FamilyPage() {
  const t = useT();
  const { can } = useBranding();
  const allowed = can("family_profiles");
  const { data, error, loading, reload } = useFamily(allowed);

  if (!allowed) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-xl font-semibold text-fg">{t("family.title")}</h1>
        <p className="text-sm text-fg-muted">{t("family.unavailable")}</p>
      </div>
    );
  }

  const terms = data?.terms ?? null;
  const profiles = data?.profiles ?? [];
  const reasonKey = data?.reason && REASONS.has(data.reason) ? data.reason : null;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <UsersRound className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-fg">{t("family.title")}</h1>
      </div>

      {loading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {!loading && error != null && <p className="text-sm text-danger">{error || t("family.errLoad")}</p>}

      {!loading && data && !data.enabled && profiles.length === 0 && (
        <Card>
          <p className="text-sm text-fg-muted">{t("family.unavailable")}</p>
        </Card>
      )}

      {!loading && data && (data.enabled || profiles.length > 0) && (
        <>
          <div className="card-hero rounded-2xl p-5">
            <p className="text-sm text-fg">{t("family.intro")}</p>
            {terms && (
              <p className="mt-2 text-xs text-fg-muted">
                {data.plan_name
                  ? t("family.terms", {
                      plan: data.plan_name,
                      max: terms.max_profiles,
                      devices: terms.devices_per_profile,
                    })
                  : t("family.termsNoName", { max: terms.max_profiles, devices: terms.devices_per_profile })}
                {" · "}
                {t("family.used", { used: data.used ?? profiles.length, max: terms.max_profiles })}
              </p>
            )}
          </div>

          <Card>
            <CardHeader title={t("family.addTitle")} />
            {data.available ? (
              <AddProfile onCreated={() => void reload()} />
            ) : (
              <p className="text-sm text-fg-muted">
                {t("family.cantAdd", { reason: t(`family.reason.${reasonKey ?? "not_active"}`) })}
              </p>
            )}
          </Card>

          {profiles.length === 0 ? (
            <p className="py-4 text-center text-sm text-fg-subtle">{t("family.empty")}</p>
          ) : (
            <div className="flex flex-col gap-3">
              {profiles.map((p) => (
                <ProfileCard
                  key={p.id}
                  profile={p}
                  resetEnabled={data.reset_devices?.enabled ?? true}
                  onChanged={() => void reload()}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
