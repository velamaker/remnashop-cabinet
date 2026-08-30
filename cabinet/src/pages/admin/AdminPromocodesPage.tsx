import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, ToggleLeft, ToggleRight, AlertCircle, X, ChevronLeft, ChevronRight, Shuffle } from "lucide-react";
import { promocodesAdminApi, plansAdminApi, type AdminPromocode, type AdminPlan } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";

const LIMIT = 25;

// Значения ДОЛЖНЫ совпадать с enum бота (src/core/enums.py):
// PromocodeRewardType / PromocodeAvailability. Иначе бэкенд вернёт 400.
const REWARD_TYPES: { value: string; label: string }[] = [
  { value: "DURATION", label: "Дни подписки" },
  { value: "TRAFFIC", label: "Трафик (ГБ)" },
  { value: "DEVICES", label: "Устройства" },
  { value: "SUBSCRIPTION", label: "Тариф (подписка)" },
  { value: "PERSONAL_DISCOUNT", label: "Личная скидка (%)" },
  { value: "PURCHASE_DISCOUNT", label: "Скидка на покупку (%)" },
];

const AVAILABILITY_OPTIONS: { value: string; label: string }[] = [
  { value: "ALL", label: "Все пользователи" },
  { value: "NEW", label: "Только новые" },
  { value: "EXISTING", label: "Существующие" },
  { value: "INVITED", label: "Приглашённые (по реф-ссылке)" },
];

// Настройка поля «значение» под каждый тип награды.
function rewardMeta(type: string): { label: string; hint: string; placeholder: string; discount: boolean; subscription: boolean } {
  switch (type) {
    case "DURATION":
      return { label: "Дней подписки", hint: "0 — бессрочно", placeholder: "30", discount: false, subscription: false };
    case "TRAFFIC":
      return { label: "Трафик, ГБ", hint: "0 — безлимит", placeholder: "50", discount: false, subscription: false };
    case "DEVICES":
      return { label: "Устройств", hint: "0 — без лимита", placeholder: "3", discount: false, subscription: false };
    case "SUBSCRIPTION":
      return { label: "Тариф", hint: "", placeholder: "", discount: false, subscription: true };
    case "PERSONAL_DISCOUNT":
    case "PURCHASE_DISCOUNT":
      return { label: "Скидка, %", hint: "от 1 до 100", placeholder: "20", discount: true, subscription: false };
    default:
      return { label: "Значение", hint: "", placeholder: "", discount: false, subscription: false };
  }
}

const REWARD_LABEL: Record<string, string> = Object.fromEntries(
  REWARD_TYPES.map((t) => [t.value, t.label]),
);

const AVAILABILITY_LABEL: Record<string, string> = Object.fromEntries(
  AVAILABILITY_OPTIONS.map((a) => [a.value, a.label]),
);

// Пункты конфигуратора: какой раскрыт сейчас.
type RowKey = "code" | "type" | "reward" | "availability" | "expires" | "limit";

// Код той же формы, что генерирует бот: 6 знаков, буквы обоих регистров и цифры.
// Похожие друг на друга символы (0/O, 1/l/I) выкинуты — код часто диктуют голосом
// и переписывают со скриншота. Уникальность стережёт база: на колонке code
// уникальный индекс, повтор вернётся ошибкой, а не молча перезапишет чужой.
const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";

function generateCode(length = 6): string {
  const values = new Uint32Array(length);
  crypto.getRandomValues(values);
  return Array.from(values, (v) => CODE_ALPHABET[v % CODE_ALPHABET.length]).join("");
}

// datetime-local отдаёт «2026-09-01T14:30» — показываем по-человечески.
function formatDateTimeLocal(value: string): string {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

// Человеко-читаемое значение награды с единицей — для таблицы.
function rewardValueText(p: AdminPromocode): string {
  if (p.reward_type === "SUBSCRIPTION") {
    const snap = p.plan_snapshot as { name?: string; duration?: number } | null | undefined;
    if (snap?.name) return snap.duration ? `${snap.name} · ${snap.duration} дн.` : snap.name;
    return "тариф";
  }
  const reward = p.reward;
  if (reward == null) return "—";
  switch (p.reward_type) {
    case "DURATION":
      return reward === 0 ? "бессрочно" : `${reward} дн.`;
    case "TRAFFIC":
      return reward === 0 ? "безлимит" : `${reward} ГБ`;
    case "DEVICES":
      return reward === 0 ? "без лимита" : `${reward} шт.`;
    case "PERSONAL_DISCOUNT":
    case "PURCHASE_DISCOUNT":
      return `${reward}%`;
    default:
      return String(reward);
  }
}

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [code, setCode] = useState("");
  const [rewardType, setRewardType] = useState("DURATION");
  const [reward, setReward] = useState("");
  const [availability, setAvailability] = useState("ALL");
  const [isReusable, setIsReusable] = useState(false);
  const [maxActivations, setMaxActivations] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Для типа SUBSCRIPTION — выбор тарифа и длительности.
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [planId, setPlanId] = useState<number | "">("");
  const [duration, setDuration] = useState<number | "">("");

  const meta = rewardMeta(rewardType);
  const [openRow, setOpenRow] = useState<RowKey | null>("code");

  // Как награда выглядит в карточке состояния: то же, что человек увидит потом
  // в списке промокодов, но собранное из полей формы, а не из ответа сервера.
  const rewardSummary = (): string => {
    if (meta.subscription) {
      const plan = plans.find((pl) => pl.id === planId);
      if (!plan) return "—";
      return duration === "" ? plan.name : `${plan.name} · ${duration} дн.`;
    }
    if (reward.trim() === "") return "—";
    const n = Number(reward);
    if (Number.isNaN(n)) return "—";
    switch (rewardType) {
      case "DURATION":
        return n === 0 ? "бессрочно" : `${n} дн.`;
      case "TRAFFIC":
        return n === 0 ? "безлимит" : `${n} ГБ`;
      case "DEVICES":
        return n === 0 ? "без лимита" : `${n} шт.`;
      case "PERSONAL_DISCOUNT":
      case "PURCHASE_DISCOUNT":
        return `${n}%`;
      default:
        return String(n);
    }
  };

  // Тарифы подгружаем один раз при первом выборе типа «Тариф».
  useEffect(() => {
    if (meta.subscription && plans.length === 0) {
      plansAdminApi
        .list()
        .then((r) => setPlans(r.items ?? []))
        .catch(() => {});
    }
  }, [meta.subscription, plans.length]);

  const selectedPlan = plans.find((p) => p.id === planId);
  const durations = selectedPlan?.durations ?? [];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;

    const base = {
      code: code.trim().toUpperCase(),
      reward_type: rewardType,
      availability,
      is_reusable: isReusable,
      max_activations: maxActivations ? Number(maxActivations) : undefined,
      expires_at: expiresAt || undefined,
    };

    let payload: Parameters<typeof promocodesAdminApi.create>[0];
    if (meta.subscription) {
      if (planId === "" || duration === "") {
        setError("Выберите тариф и длительность");
        return;
      }
      payload = { ...base, plan_id: Number(planId), duration: Number(duration) };
    } else {
      // reward пустой → не отправляем (0 — валидное значение, проверяем строку).
      const rewardNum = reward.trim() !== "" ? Number(reward) : undefined;
      if (rewardNum == null || Number.isNaN(rewardNum)) {
        setError(`Укажите значение (${meta.label.toLowerCase()})`);
        return;
      }
      if (meta.discount && (rewardNum < 1 || rewardNum > 100)) {
        setError("Скидка должна быть от 1 до 100%");
        return;
      }
      if (!meta.discount && rewardNum < 0) {
        setError("Значение не может быть отрицательным");
        return;
      }
      payload = { ...base, reward: rewardNum };
    }

    setSaving(true);
    setError(null);
    try {
      await promocodesAdminApi.create(payload);
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Ошибка создания");
    } finally {
      setSaving(false);
    }
  };

  // ── Пошаговый конфигуратор, как в боте ──────────────────────────────────────
  // Наверху — карточка с текущим состоянием (что именно получится), ниже строки
  // по пунктам: жмёшь пункт — он раскрывается редактором прямо на месте. Одна
  // длинная форма показывала все поля разом, включая те, что к выбранному типу
  // награды отношения не имеют; здесь на экране только то, что сейчас меняешь.
  const summary: { label: string; value: string; dim?: boolean }[] = [
    { label: "Код", value: code || "не задан", dim: !code },
    { label: "Тип", value: REWARD_LABEL[rewardType] ?? rewardType },
    { label: "Награда", value: rewardSummary(), dim: rewardSummary() === "—" },
    { label: "Доступ", value: AVAILABILITY_LABEL[availability] ?? availability },
    { label: "Повторная активация", value: isReusable ? "Разрешена" : "Запрещена" },
    { label: "Действует до", value: expiresAt ? formatDateTimeLocal(expiresAt) : "∞", dim: !expiresAt },
    { label: "Лимит активаций", value: maxActivations || "∞", dim: !maxActivations },
  ];

  const rows: { key: RowKey; label: string; value: string }[] = [
    { key: "code", label: "Код", value: code || "не задан" },
    { key: "type", label: "Тип награды", value: REWARD_LABEL[rewardType] ?? rewardType },
    { key: "reward", label: meta.subscription ? "Тариф и срок" : "Награда", value: rewardSummary() },
    { key: "availability", label: "Доступ", value: AVAILABILITY_LABEL[availability] ?? availability },
    { key: "expires", label: "Срок действия", value: expiresAt ? formatDateTimeLocal(expiresAt) : "∞" },
    { key: "limit", label: "Лимит активаций", value: maxActivations || "∞" },
  ];

  const rowButton = (r: { key: RowKey; label: string; value: string }) => (
    <button
      type="button"
      onClick={() => setOpenRow(openRow === r.key ? null : r.key)}
      className={`flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-subtle ${
        openRow === r.key ? "bg-bg-subtle" : ""
      }`}
    >
      <span className="text-sm text-fg">{r.label}</span>
      <span className="flex items-center gap-2">
        <span className="max-w-[10rem] truncate text-sm text-fg-muted">{r.value}</span>
        <ChevronRight
          className={`h-4 w-4 flex-shrink-0 text-fg-subtle transition-transform ${
            openRow === r.key ? "rotate-90" : ""
          }`}
        />
      </span>
    </button>
  );

  const inputCls =
    "w-full rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2.5 text-sm text-fg placeholder:text-fg-muted focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-md flex-col rounded-2xl border border-border-subtle bg-bg shadow-xl">
        <div className="flex items-center justify-between border-b border-border-subtle px-6 py-4">
          <h2 className="text-base font-semibold text-fg">Конфигуратор промокода</h2>
          <button onClick={onClose} className="rounded-lg p-1 text-fg-muted hover:text-fg">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5 space-y-4">
            {/* Карточка состояния: что получится, если нажать «Создать» прямо сейчас. */}
            <div className="rounded-xl border-l-2 border-accent bg-bg-subtle px-4 py-3">
              <dl className="space-y-1">
                {summary.map((s) => (
                  <div key={s.label} className="flex items-baseline justify-between gap-3">
                    <dt className="text-xs text-fg-muted">{s.label}</dt>
                    <dd className={`text-right text-xs font-medium ${s.dim ? "text-fg-subtle" : "text-fg"}`}>
                      {s.value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <p className="text-xs text-fg-subtle">Выберите пункт для изменения.</p>

            <div className="divide-y divide-border-subtle overflow-hidden rounded-xl border border-border-subtle">
              {rows.map((r) => (
                <div key={r.key}>
                  {rowButton(r)}
                  {openRow === r.key && (
                    <div className="border-t border-border-subtle bg-bg-subtle/50 px-4 py-3 space-y-2">
                      {r.key === "code" && (
                        <>
                          <div className="flex gap-2">
                            <input
                              type="text"
                              value={code}
                              onChange={(e) => setCode(e.target.value.toUpperCase())}
                              placeholder="SUMMER2025"
                              className={inputCls}
                            />
                            <button
                              type="button"
                              onClick={() => setCode(generateCode())}
                              title="Сгенерировать код"
                              className="flex-shrink-0 rounded-xl border border-border-subtle px-3 text-sm font-medium text-fg-muted transition-colors hover:text-fg"
                            >
                              <Shuffle className="h-4 w-4" />
                            </button>
                          </div>
                          <p className="text-[11px] text-fg-subtle">
                            Можно придумать свой или нажать кнопку — код сгенерируется случайно.
                          </p>
                        </>
                      )}

                      {r.key === "type" && (
                        <select
                          value={rewardType}
                          onChange={(e) => setRewardType(e.target.value)}
                          className={inputCls}
                        >
                          {REWARD_TYPES.map((t) => (
                            <option key={t.value} value={t.value}>{t.label}</option>
                          ))}
                        </select>
                      )}

                      {r.key === "reward" && meta.subscription && (
                        <>
                          <select
                            value={planId}
                            onChange={(e) => {
                              setPlanId(e.target.value ? Number(e.target.value) : "");
                              setDuration("");
                            }}
                            className={inputCls}
                          >
                            <option value="">— выберите тариф —</option>
                            {plans.map((pl) => (
                              <option key={pl.id} value={pl.id}>{pl.name}</option>
                            ))}
                          </select>
                          <select
                            value={duration}
                            onChange={(e) => setDuration(e.target.value ? Number(e.target.value) : "")}
                            disabled={!selectedPlan}
                            className={`${inputCls} disabled:opacity-50`}
                          >
                            <option value="">{selectedPlan ? "— выберите срок —" : "сначала тариф"}</option>
                            {durations.map((d) => (
                              <option key={d.days} value={d.days}>{d.days} дн.</option>
                            ))}
                          </select>
                          <p className="text-[11px] text-fg-subtle">
                            Промокод выдаст этот тариф на выбранный срок.
                          </p>
                        </>
                      )}

                      {r.key === "reward" && !meta.subscription && (
                        <>
                          <input
                            type="number"
                            value={reward}
                            onChange={(e) => setReward(e.target.value)}
                            placeholder={meta.placeholder}
                            min={meta.discount ? 1 : 0}
                            max={meta.discount ? 100 : undefined}
                            className={inputCls}
                          />
                          {meta.hint && <p className="text-[11px] text-fg-subtle">{meta.hint}</p>}
                        </>
                      )}

                      {r.key === "availability" && (
                        <select
                          value={availability}
                          onChange={(e) => setAvailability(e.target.value)}
                          className={inputCls}
                        >
                          {AVAILABILITY_OPTIONS.map((a) => (
                            <option key={a.value} value={a.value}>{a.label}</option>
                          ))}
                        </select>
                      )}

                      {r.key === "expires" && (
                        <>
                          <input
                            type="datetime-local"
                            value={expiresAt}
                            onChange={(e) => setExpiresAt(e.target.value)}
                            className={inputCls}
                          />
                          {expiresAt && (
                            <button
                              type="button"
                              onClick={() => setExpiresAt("")}
                              className="text-[11px] text-fg-muted underline hover:text-fg"
                            >
                              Сделать бессрочным
                            </button>
                          )}
                        </>
                      )}

                      {r.key === "limit" && (
                        <>
                          <input
                            type="number"
                            min={1}
                            value={maxActivations}
                            onChange={(e) => setMaxActivations(e.target.value)}
                            placeholder="∞"
                            className={inputCls}
                          />
                          <p className="text-[11px] text-fg-subtle">Пусто — без ограничения.</p>
                        </>
                      )}
                    </div>
                  )}
                </div>
              ))}

              {/* Повтор — переключатель, разворачивать нечего. */}
              <label className="flex w-full cursor-pointer items-center justify-between gap-3 px-4 py-3">
                <span className="text-sm text-fg">Повторная активация</span>
                <span className="flex items-center gap-2">
                  <span className="text-sm text-fg-muted">{isReusable ? "Разрешена" : "Запрещена"}</span>
                  <input
                    type="checkbox"
                    checked={isReusable}
                    onChange={(e) => setIsReusable(e.target.checked)}
                    className="h-4 w-4 rounded border-border accent-accent"
                  />
                </span>
              </label>
            </div>

            {error && <p className="rounded-xl bg-danger/10 px-4 py-2 text-sm text-danger">{error}</p>}
          </div>

          <div className="flex gap-3 border-t border-border-subtle px-6 py-4">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-xl border border-border-subtle px-4 py-2.5 text-sm font-medium text-fg-muted transition-colors hover:text-fg"
            >
              Отмена
            </button>
            <button
              type="submit"
              disabled={saving || !code.trim()}
              className="flex-1 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg transition-colors hover:bg-accent/90 disabled:opacity-50"
            >
              {saving ? "Создание…" : "Создать промокод"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function AdminPromocodesPage() {
  const [items, setItems] = useState<AdminPromocode[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    promocodesAdminApi
      .list({ limit: LIMIT, offset })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка"))
      .finally(() => setLoading(false));
  }, [offset]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (id: number, is_active: boolean) => {
    setActionId(id);
    try {
      await promocodesAdminApi.toggle(id, !is_active);
      load();
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setActionId(null);
    }
  };

  const remove = async (id: number, code: string) => {
    if (!confirm(`Удалить промокод ${code}?`)) return;
    setActionId(id);
    try {
      await promocodesAdminApi.delete(id);
      load();
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setActionId(null);
    }
  };

  const totalPages = Math.ceil(total / LIMIT);
  const currentPage = Math.floor(offset / LIMIT) + 1;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">Промокоды</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Создать
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-border-subtle">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-subtle">
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted">Код</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted">Тип</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden sm:table-cell">Значение</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden md:table-cell">Активации</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden lg:table-cell">Истекает</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted">Статус</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-fg-muted">Действия</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center">
                    <div className="inline-block h-7 w-7 animate-spin rounded-full border-2 border-border border-t-accent" />
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-fg-muted">
                    Промокодов нет
                  </td>
                </tr>
              ) : (
                items.map((p) => (
                  <tr
                    key={p.id}
                    className="border-b border-border-subtle last:border-0 hover:bg-bg-raised transition-colors"
                  >
                    <td className="px-4 py-3">
                      <span className="font-mono font-semibold text-fg">{p.code}</span>
                    </td>
                    <td className="px-4 py-3 text-fg-muted">
                      {REWARD_LABEL[p.reward_type] ?? p.reward_type}
                      {/* Моб.: значение/активации/срок скрыты столбцами — показываем строкой */}
                      <div className="mt-0.5 text-[11px] text-fg-subtle md:hidden">
                        {rewardValueText(p)} · {p.total_activations ?? 0}{p.max_activations != null ? `/${p.max_activations}` : ""} акт.{p.expires_at ? ` · до ${formatDate(p.expires_at)}` : ""}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-fg-muted hidden sm:table-cell">
                      {rewardValueText(p)}
                    </td>
                    <td className="px-4 py-3 text-fg-muted hidden md:table-cell">
                      {p.total_activations ?? 0}
                      {p.max_activations != null && ` / ${p.max_activations}`}
                    </td>
                    <td className="px-4 py-3 text-xs text-fg-muted hidden lg:table-cell">
                      {p.expires_at ? formatDate(p.expires_at) : "∞"}
                    </td>
                    <td className="px-4 py-3">
                      {p.is_active ? (
                        <span className="rounded-full bg-success/10 px-2 py-0.5 text-xs text-success">
                          Активен
                        </span>
                      ) : (
                        <span className="rounded-full bg-fg-subtle/20 px-2 py-0.5 text-xs text-fg-muted">
                          Отключён
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => toggle(p.id, p.is_active)}
                          disabled={actionId === p.id}
                          className="rounded-lg p-1.5 text-fg-muted hover:text-accent transition-colors disabled:opacity-40"
                          title={p.is_active ? "Отключить" : "Включить"}
                        >
                          {p.is_active ? (
                            <ToggleRight className="h-5 w-5" />
                          ) : (
                            <ToggleLeft className="h-5 w-5" />
                          )}
                        </button>
                        <button
                          onClick={() => remove(p.id, p.code)}
                          disabled={actionId === p.id}
                          className="rounded-lg p-1.5 text-fg-muted hover:text-danger transition-colors disabled:opacity-40"
                          title="Удалить"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-fg-muted">
            Страница {currentPage} из {totalPages}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              disabled={offset === 0 || loading}
              className="rounded-xl border border-border-subtle p-2 text-fg-muted hover:text-fg disabled:opacity-40 transition-colors"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              onClick={() => setOffset(offset + LIMIT)}
              disabled={offset + LIMIT >= total || loading}
              className="rounded-xl border border-border-subtle p-2 text-fg-muted hover:text-fg disabled:opacity-40 transition-colors"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={load}
        />
      )}
    </div>
  );
}
