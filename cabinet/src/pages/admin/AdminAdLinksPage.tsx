import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, BarChart2, AlertCircle, X, Copy, Check } from "lucide-react";
import { adLinksAdminApi, type AdminAdLink } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { adCodeProblem, suggestAdCode } from "@/lib/adLinkCode";
import { useBranding } from "@/contexts/BrandingContext";
import { botHas } from "@/lib/botCapabilities";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const t = useT();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  // Пока код не трогали руками, он следует за названием. Стоит человеку поправить
  // код — подсказка больше не перетирает его ввод.
  const [codeTouched, setCodeTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<AdminAdLink | null>(null);
  const [copied, setCopied] = useState(false);
  // Готовую ссылку отдаёт только бот новее 1.3.8. Со старым ботом «появится, когда
  // Telegram ответит» было бы обещанием, которое не сбудется никогда.
  const { appearance } = useBranding();
  const botGivesUrl = botHas(appearance, "ad_link_url");

  const codeError = adCodeProblem(code);
  const canSubmit = name.trim() !== "" && code.trim() !== "" && !codeError && !saving;

  const changeName = (value: string) => {
    setName(value);
    if (!codeTouched) setCode(suggestAdCode(value));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      const link = await adLinksAdminApi.create({ name: name.trim(), code: code.trim() });
      onCreated();
      // Не закрываем сразу: главное, ради чего создавали, — ссылка для рекламы.
      // Показываем её здесь же, с копированием, а не отправляем искать в списке.
      setCreated(link);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.adlinks.error"));
    } finally {
      setSaving(false);
    }
  };

  const copyCreated = () => {
    if (!created) return;
    navigator.clipboard.writeText(created.url || created.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-border-subtle bg-bg shadow-xl">
        <div className="flex items-center justify-between border-b border-border-subtle px-6 py-4">
          <h2 className="text-base font-semibold text-fg">{created ? t("adm.adlinks.created_title") : t("adm.adlinks.create_title")}</h2>
          <button onClick={onClose} className="rounded-lg p-1 text-fg-muted hover:text-fg" aria-label={t("adm.adlinks.close")}><X className="h-5 w-5" /></button>
        </div>
        {created ? (
          <div className="space-y-4 px-6 py-5">
            <p className="text-sm text-fg-muted">
              {t("adm.adlinks.created_hint", { name: created.name })}
            </p>
            <div className="flex items-center gap-2 rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2.5">
              <span className="min-w-0 flex-1 break-all font-mono text-sm text-fg">{created.url || created.code}</span>
              <button type="button" onClick={copyCreated} className="shrink-0 rounded-lg p-1.5 text-fg-muted hover:text-accent" title={t("adm.adlinks.copy_link")}>
                {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
              </button>
            </div>
            {!created.url && (
              <p className="text-xs text-fg-muted">
                {botGivesUrl
                  ? t("adm.adlinks.url_pending")
                  : t("adm.adlinks.url_old_bot", { code: created.code })}
              </p>
            )}
            <button type="button" onClick={onClose} className="w-full rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 transition-colors">
              {t("adm.adlinks.done")}
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 px-6 py-5">
            <div>
              <label htmlFor="ad-link-name" className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.adlinks.name_label")}</label>
              {/* Подсказка начинается с «Например:» — прежний серый «Instagram Story»
                  принимали за уже введённый текст и не понимали, почему форма не создаётся. */}
              <input id="ad-link-name" value={name} onChange={e => changeName(e.target.value)} required className="input w-full" placeholder={t("adm.adlinks.name_placeholder")} />
              <p className="mt-1 text-xs text-fg-muted">{t("adm.adlinks.name_hint")}</p>
            </div>
            <div>
              <label htmlFor="ad-link-code" className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.adlinks.code_label")}</label>
              <input
                id="ad-link-code"
                value={code}
                onChange={e => { setCode(e.target.value); setCodeTouched(true); }}
                required
                className="input w-full font-mono"
                placeholder={t("adm.adlinks.code_placeholder")}
                aria-invalid={codeError ? true : undefined}
              />
              {codeError ? (
                <p className="mt-1 text-xs text-danger">{codeError}</p>
              ) : (
                <p className="mt-1 text-xs text-fg-muted">
                  {t("adm.adlinks.code_hint")}
                </p>
              )}
            </div>
            {error && <p className="rounded-xl bg-danger/10 px-4 py-2 text-sm text-danger">{error}</p>}
            <div className="flex gap-3">
              <button type="button" onClick={onClose} className="flex-1 rounded-xl border border-border-subtle px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition-colors">{t("adm.adlinks.cancel")}</button>
              <button type="submit" disabled={!canSubmit} className="flex-1 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50 transition-colors">
                {saving ? t("adm.adlinks.creating") : t("adm.adlinks.create")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function StatsModal({ link, onClose }: { link: AdminAdLink; onClose: () => void }) {
  const t = useT();
  const [data, setData] = useState<AdminAdLink | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adLinksAdminApi.stats(link.id)
      .then(setData)
      .finally(() => setLoading(false));
  }, [link.id]);

  const s = data?.stats;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-2xl border border-border-subtle bg-bg shadow-xl">
        <div className="flex items-center justify-between border-b border-border-subtle px-6 py-4">
          <div>
            <h2 className="text-base font-semibold text-fg">{link.name}</h2>
            <p className="text-xs font-mono text-fg-muted">{link.code}</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-fg-muted hover:text-fg" aria-label={t("adm.adlinks.close")}><X className="h-5 w-5" /></button>
        </div>
        <div className="px-6 py-5">
          {loading ? (
            <div className="flex justify-center py-10"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
          ) : !s ? (
            <p className="text-center text-fg-muted py-10">{t("adm.adlinks.no_data")}</p>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-xl bg-bg-subtle p-4 text-center">
                  <p className="text-3xl font-bold text-fg">{s.registrations}</p>
                  <p className="text-xs text-fg-muted mt-1">{t("adm.adlinks.stat_registrations")}</p>
                </div>
                <div className="rounded-xl bg-bg-subtle p-4 text-center">
                  <p className="text-3xl font-bold text-fg">{s.trials}</p>
                  <p className="text-xs text-fg-muted mt-1">{t("adm.adlinks.stat_trials")}</p>
                </div>
                <div className="rounded-xl bg-success/10 p-4 text-center">
                  <p className="text-3xl font-bold text-success">{s.buyers}</p>
                  <p className="text-xs text-fg-muted mt-1">{t("adm.adlinks.stat_buyers")}</p>
                </div>
                <div className="rounded-xl bg-accent/10 p-4 text-center">
                  <p className="text-3xl font-bold text-accent">{(s.reg_to_buy_rate * 100).toFixed(1)}%</p>
                  <p className="text-xs text-fg-muted mt-1">{t("adm.adlinks.stat_conversion")}</p>
                </div>
              </div>
              {Object.keys(s.revenue).length > 0 && (
                <div className="rounded-xl border border-border-subtle p-4">
                  <p className="mb-2 text-xs font-semibold text-fg-muted">{t("adm.adlinks.revenue")}</p>
                  {Object.entries(s.revenue).map(([cur, amount]) => (
                    <div key={cur} className="flex items-center justify-between">
                      <span className="text-sm text-fg-muted">{cur}</span>
                      <span className="font-semibold text-fg">{Number(amount).toLocaleString("ru")}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AdminAdLinksPage() {
  const t = useT();
  const [links, setLinks] = useState<AdminAdLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [statsLink, setStatsLink] = useState<AdminAdLink | null>(null);
  const [copied, setCopied] = useState<number | null>(null);

  // translate, а не t: иначе load зависел бы от языка и смена языка перезапрашивала
  // бы список.
  const load = useCallback(() => {
    setLoading(true);
    adLinksAdminApi.list()
      .then(r => setLinks(r.items))
      .catch(e => setError(e instanceof ApiError ? e.detail : translate("adm.adlinks.error")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const remove = async (link: AdminAdLink) => {
    if (!confirm(t("adm.adlinks.confirm_delete", { name: link.name }))) return;
    try {
      await adLinksAdminApi.delete(link.id);
      load();
    } catch (e) { alert(e instanceof ApiError ? e.detail : t("adm.adlinks.error")); }
  };

  const toggleActive = async (link: AdminAdLink) => {
    try {
      await adLinksAdminApi.update(link.id, { is_active: !link.is_active });
      load();
    } catch (e) { alert(e instanceof ApiError ? e.detail : t("adm.adlinks.error")); }
  };

  const copyCode = (link: AdminAdLink) => {
    // Копируем готовую ссылку, если бэкенд её дал: именно её владелец вставляет
    // в рекламу. Голый код без адреса никуда не ведёт.
    navigator.clipboard.writeText(link.url || link.code);
    setCopied(link.id);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">{t("adm.adlinks.title")}</h1>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 transition-colors">
          <Plus className="h-4 w-4" /> {t("adm.adlinks.create")}
        </button>
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : links.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">{t("adm.adlinks.empty")}</div>
      ) : (
        <div className="space-y-3">
          {links.map(link => (
            <div key={link.id} className="flex items-center gap-4 rounded-2xl border border-border-subtle bg-bg-subtle px-5 py-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-fg truncate">{link.name}</span>
                  {!link.is_active && <span className="rounded-full bg-fg-subtle/20 px-2 py-0.5 text-xs text-fg-muted">{t("adm.adlinks.off_badge")}</span>}
                </div>
                <div className="mt-0.5 flex items-center gap-2">
                  <span className="font-mono text-xs text-fg-muted truncate">{link.url || link.code}</span>
                  {link.created_at && <span className="text-xs text-fg-subtle">· {formatDate(link.created_at)}</span>}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={() => copyCode(link)} className="rounded-lg p-1.5 text-fg-muted hover:text-accent transition-colors" title={t("adm.adlinks.copy_link")}>
                  {copied === link.id ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
                </button>
                <button onClick={() => setStatsLink(link)} className="rounded-lg p-1.5 text-fg-muted hover:text-accent transition-colors" title={t("adm.adlinks.stats")}>
                  <BarChart2 className="h-4 w-4" />
                </button>
                <button
                  onClick={() => toggleActive(link)}
                  title={link.is_active ? t("adm.adlinks.disable") : t("adm.adlinks.enable")}
                  /* shrink-0: без него в узкой строке кнопку сжимало, и кружок
                     выезжал на соседнюю иконку удаления. */
                  className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${link.is_active ? "bg-accent" : "bg-border"}`}
                >
                  {/* Кружку нужна точка отсчёта: без left он вставал по «авто» и
                      при сдвиге вылезал за пределы дорожки. */}
                  <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${link.is_active ? "translate-x-5" : "translate-x-0"}`} />
                </button>
                <button onClick={() => remove(link)} className="rounded-lg p-1.5 text-fg-muted hover:text-danger transition-colors" title={t("adm.adlinks.delete")}>
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreated={load} />}
      {statsLink && <StatsModal link={statsLink} onClose={() => setStatsLink(null)} />}
    </div>
  );
}
