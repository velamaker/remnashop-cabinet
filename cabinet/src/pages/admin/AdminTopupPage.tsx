import { useEffect, useState } from "react";
import { Coins, Save, RotateCcw, AlertCircle, Info } from "lucide-react";
import {
  topupMethodsAdminApi,
  type TopupMethodRules,
  type TopupMethodsResponse,
  type TopupMethodUpdate,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { TopupSettingsCard } from "./AdminSettingsPage";

// Раздел «Пополнение баланса» — вынесен из «Настроек» в отдельный пункт панели.
//
// Две карточки, и они про РАЗНОЕ. Сверху — сводные условия пополнения (наш
// собственный бэкенд хранит их одним блоком). Снизу — правила по каждому способу
// оплаты: их присылает только бэкенд, у которого условия так и устроены (адаптер
// поверх чужого бота). У нашего собственного этой ручки нет вовсе — она отвечает
// 404, карточка не показывается, и раздел выглядит ровно как до этой правки.
export default function AdminTopupPage() {
  const [data, setData] = useState<TopupMethodsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Сводную карточку рисуем только ПОСЛЕ этого запроса: именно он приносит карту
  // применимости её полей. Нарисуй мы её раньше — на бэкенде, где сохранять
  // нечего, на долю секунды появилась бы кнопка «Сохранить», и по ней успевают
  // нажать. Ответа нет вовсе (наш собственный бэкенд, 404) — карты нет, карточка
  // работает как раньше.
  const [probed, setProbed] = useState(false);

  useEffect(() => {
    topupMethodsAdminApi
      .get()
      .then(setData)
      // 404 — «у этого бэкенда правил по способам нет» (наш собственный), 501 —
      // «раздел не переведён» у адаптера. Ни то, ни другое не беда: карточки
      // просто не будет. Всё остальное — настоящая ошибка, и о ней надо сказать.
      .catch((e) => {
        if (e instanceof ApiError && (e.status === 404 || e.status === 501)) return;
        setError(e instanceof ApiError ? e.detail : "Не удалось загрузить способы оплаты");
      })
      .finally(() => setProbed(true));
  }, []);

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Coins className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Пополнение баланса</h1>
      </div>
      {probed && <TopupSettingsCard applicability={data?.summary} />}
      {error && (
        <div className="flex items-start gap-2 rounded-2xl border border-danger/30 bg-danger/5 p-4 text-sm text-danger">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {data && <TopupMethodsCard data={data} />}
    </div>
  );
}

// ─── Правила пополнения по способам оплаты ───────────────────────────────────

const inputCls =
  "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

/** Сумма для поля ввода. Дробные суммы у бота законны, поэтому печатаем как есть,
 *  без принудительных копеек: «100», а не «100.00». */
const money = (value: number) => String(value);

/** Быстрые суммы для поля ввода — тем же видом, в каком их вводит человек. */
const presetsText = (values: number[]) => values.map(money).join(", ");

function StatusBadge({ item }: { item: TopupMethodRules }) {
  // Три состояния, а не два: «выключен» и «включён, но провайдер не настроен» —
  // разные новости. Во втором случае способ включён, а покупатель его всё равно
  // не увидит, и молчать об этом нельзя.
  const [text, cls] = !item.is_active
    ? ["Выключен", "border-border-subtle bg-bg text-fg-muted"]
    : item.is_configured
      ? ["Включён", "border-success/30 bg-success/10 text-success"]
      : ["Включён, но не настроен", "border-warning/30 bg-warning/10 text-warning"];
  return <span className={`shrink-0 rounded-lg border px-2 py-0.5 text-[11px] ${cls}`}>{text}</span>;
}

function MethodRow({
  item,
  onSaved,
}: {
  item: TopupMethodRules;
  onSaved: (fresh: TopupMethodRules) => void;
}) {
  // Пустое поле = «как у бота по умолчанию». Именно поэтому в поле кладём пустую
  // строку, когда своего значения нет: цифра в поле читалась бы как своя
  // настройка, и владелец «сохранял» бы умолчание себе в переопределение.
  const initMin = item.min_custom ? money(item.min_amount) : "";
  const initMax = item.max_custom ? money(item.max_amount) : "";
  const initPresets = item.presets_custom ? presetsText(item.presets) : "";
  const [min, setMin] = useState(initMin);
  const [max, setMax] = useState(initMax);
  const [presets, setPresets] = useState(initPresets);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Отправляем ТОЛЬКО тронутые поля. Иначе правка одного минимума уносила бы с
  // собой пустое поле быстрых сумм — а пустое поле означает «кнопок нет», и
  // владелец, поднявший минимум, молча остался бы без кнопок пополнения.
  const dirty = { min: min !== initMin, max: max !== initMax, presets: presets !== initPresets };
  const changed = dirty.min || dirty.max || dirty.presets;

  const apply = (fresh: TopupMethodRules) => {
    setMin(fresh.min_custom ? money(fresh.min_amount) : "");
    setMax(fresh.max_custom ? money(fresh.max_amount) : "");
    setPresets(fresh.presets_custom ? presetsText(fresh.presets) : "");
    onSaved(fresh);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const send = async (body: TopupMethodUpdate) => {
    setSaving(true);
    setErr(null);
    try {
      apply(await topupMethodsAdminApi.update(item.method_id, body));
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  // «100,50» человек пишет и как «сто рублей пятьдесят копеек», и как две суммы.
  // В поле одной суммы запятая — это разделитель дробной части (так же её
  // понимает бэкенд), поэтому нормализуем и проверяем: молча превратить непонятое
  // в «вернуть умолчание» нельзя — это стёрло бы настройку.
  const amount = (raw: string): number | null | undefined => {
    const text = raw.trim().replace(",", ".");
    if (text === "") return null; // пустое поле — «вернуть умолчание бота»
    const value = Number(text);
    return Number.isFinite(value) ? value : undefined;
  };

  const save = () => {
    const body: TopupMethodUpdate = {};
    if (dirty.min) {
      const value = amount(min);
      if (value === undefined) {
        setErr("Мин. сумма: нужно число в рублях");
        return;
      }
      body.min_amount = value;
    }
    if (dirty.max) {
      const value = amount(max);
      if (value === undefined) {
        setErr("Макс. сумма: нужно число в рублях");
        return;
      }
      body.max_amount = value;
    }
    if (dirty.presets) {
      // Пустая строка быстрых сумм — это НЕ сброс, а «кнопок нет»: у бота это
      // разные состояния, и сброс делает отдельная кнопка «По умолчанию».
      const amounts = presets
        .split(",")
        .map((s) => s.trim().replace(/\s+/g, ""))
        .filter(Boolean)
        .map(Number);
      if (amounts.some((n) => !Number.isFinite(n))) {
        setErr("Быстрые суммы: только числа через запятую");
        return;
      }
      body.presets = amounts;
    }
    send(body);
  };

  return (
    <div className="rounded-xl border border-border-subtle bg-bg p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium text-fg">{item.name}</p>
        {/* Только состояние, без переключателя: включением способов заведует
            раздел «Шлюзы», и вторая такая же кнопка была бы копией чужой функции.
            Причина написана один раз над списком (карта `locked` от бэкенда). */}
        <StatusBadge item={item} />
        <span className="text-[11px] text-fg-muted">{item.method_id}</span>
      </div>

      {item.hints.map((hint) => (
        <p key={hint} className="mb-2 flex items-start gap-1.5 text-[11px] leading-snug text-fg-muted">
          <Info className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{hint}</span>
        </p>
      ))}

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Мин. сумма, ₽</label>
          <input
            type="text"
            inputMode="decimal"
            value={min}
            placeholder={`${money(item.min_default)} (по умолчанию)`}
            onChange={(e) => setMin(e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-fg-muted">Макс. сумма, ₽</label>
          <input
            type="text"
            inputMode="decimal"
            value={max}
            placeholder={`${money(item.max_default)} (по умолчанию)`}
            onChange={(e) => setMax(e.target.value)}
            className={inputCls}
          />
        </div>
      </div>

      <div className="mt-3">
        <label className="mb-1 block text-xs text-fg-muted">Быстрые суммы, ₽ (через запятую)</label>
        <input
          type="text"
          value={presets}
          placeholder={`${presetsText(item.presets_default)} (по умолчанию)`}
          onChange={(e) => setPresets(e.target.value)}
          className={inputCls}
        />
        <p className="mt-1 text-[11px] leading-snug text-fg-muted">
          {item.presets_custom && item.presets.length === 0
            ? "Сейчас кнопок нет. "
            : item.presets_custom
              ? ""
              : "Сейчас используются кнопки бота по умолчанию. "}
          Пустое поле — кнопок не будет вовсе; чтобы вернуть кнопки бота, нажмите «По умолчанию».
        </p>
      </div>

      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-xs">
          {err && <span className="text-danger">{err}</span>}
          {saved && !err && <span className="text-success">Сохранено</span>}
        </span>
        <div className="flex items-center gap-2">
          {(item.min_custom || item.max_custom || item.presets_custom) && (
            <button
              onClick={() => send({ min_amount: null, max_amount: null, presets: null })}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-xl border border-border-subtle px-3 py-2 text-sm text-fg-muted hover:bg-bg-subtle disabled:opacity-50"
            >
              <RotateCcw className="h-3.5 w-3.5" /> По умолчанию
            </button>
          )}
          <button
            onClick={save}
            disabled={saving || !changed}
            className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50"
          >
            <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TopupMethodsCard({ data }: { data: TopupMethodsResponse }) {
  const [items, setItems] = useState(data.items);
  const [showAll, setShowAll] = useState(false);

  const onSaved = (fresh: TopupMethodRules) =>
    setItems((list) => list.map((m) => (m.method_id === fresh.method_id ? fresh : m)));

  // Порядок — тот, в котором способы приехали от бэкенда: в этом же порядке
  // кнопки видит покупатель, и пересортировать его значило бы отправить владельца
  // искать способ не там, где он на него смотрит.
  const active = items.filter((m) => m.is_active);
  const rest = items.filter((m) => !m.is_active);
  const shown = showAll ? items : active;

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-fg">Правила по способам оплаты</h3>
        <p className="mt-0.5 text-xs text-fg-muted">
          {data.note ??
            "Минимум, максимум и быстрые суммы у каждого способа оплаты свои. Это настройки бота — экран правит их напрямую."}
        </p>
        {/* «Правится не отсюда» — один раз над списком, а не в каждой из двух
            десятков строк: повторённая двадцать раз причина перестаёт читаться. */}
        {data.methods?.locked?.method_enabled && (
          <p className="mt-1 text-xs text-fg-muted">{data.methods.locked.method_enabled}</p>
        )}
      </div>
      <div className="space-y-3">
        {shown.length === 0 && (
          <p className="text-sm text-fg-muted">Ни один способ оплаты не включён.</p>
        )}
        {shown.map((item) => (
          <MethodRow key={item.method_id} item={item} onSaved={onSaved} />
        ))}
        {rest.length > 0 && (
          <button
            onClick={() => setShowAll((v) => !v)}
            className="w-full rounded-xl border border-border-subtle px-4 py-2.5 text-sm text-fg-muted hover:bg-bg"
          >
            {showAll ? "Скрыть выключенные способы" : `Показать выключенные способы (${rest.length})`}
          </button>
        )}
      </div>
    </section>
  );
}
