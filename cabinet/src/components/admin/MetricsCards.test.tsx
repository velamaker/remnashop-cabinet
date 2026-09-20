import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, act, within } from "@testing-library/react";
import { ApiError } from "@/types/api";
import type { MetricsResponse } from "@/api/admin";
import { translate } from "@/i18n/translate";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";

// Подписи плиток теперь из словаря: ищем по ключу, а не по русской строке —
// иначе тест сломается от правки перевода, а не от поломки в коде.
const ru = (key: string, vars?: Record<string, string | number>): string =>
  translate(key, vars, "ru");

// Оговорка про чарджбэк стоит в одном абзаце с общим пояснением блока, поэтому
// ищем абзац, ТЕКСТ которого её содержит, а не совпадает с ней целиком.
const hasRefundsNote = (content: string): boolean =>
  content.includes(ru("adm.stats.kpi_refunds_note"));

// Плитка «Возвраты (30 дн)» опасна одним: нулём, который врёт. Бот узнаёт о
// возврате, только если шлюз о нём сообщает, а через шлюзы, которые молчат
// (ЮMoney, звёзды), может идти большая часть оплат — и «0 ₽» без оговорки
// читался бы как «возвратов не было». Тесты держат
// оговорку, раздельные валюты и то, что без поля (старый бэкенд, «Бедолага»)
// кабинет не ломается и лишнего не рисует.
let metrics: () => Promise<unknown> = () => Promise.resolve(base());

vi.mock("@/api/admin", () => ({
  statisticsApi: { metrics: () => metrics() },
}));

const { MetricsCards } = await import("./MetricsCards");

// Суммы в фикстуре меньше 1000: у toLocaleString("ru-RU") разделитель тысяч —
// неразрывный пробел, и строковые сравнения стали бы хрупкими.
function base(over: Partial<MetricsResponse> = {}): MetricsResponse {
  return {
    currency: "RUB",
    mrr: 450,
    mrr_subs: 3,
    arpu: 120,
    arppu: 300,
    revenue_30d: 900,
    active_users: 7,
    payers_30d: 3,
    conversion: { trials: 10, converted: 2, pct: 20 },
    churn: { active_now: 7, churned_30d: 1, pct: 12.5 },
    payments: { completed_30d: 4, canceled_30d: 1, success_pct: 80 },
    top_plans: [],
    top_gateways: [],
    ...over,
  };
}

/**
 * Плитка по подписи. Ждём именно её (или соседнюю плитку): компонент до ответа
 * рисует пустоту, и проверка «плитки нет», сделанная до загрузки, прошла бы впустую.
 */
async function tile(label: string): Promise<HTMLElement> {
  const node = await screen.findByText(label);
  return node.parentElement as HTMLElement;
}

// Язык прибиваем к русскому: сравниваем с translate(..., "ru"), а в jsdom
// язык «устройства» английский — без этого тест сравнивал бы разные языки.
function renderCards() {
  localStorage.setItem(STORAGE_KEY, "ru");
  return render(
    <I18nProvider>
      <MetricsCards />
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  localStorage.removeItem(STORAGE_KEY);
  metrics = () => Promise.resolve(base());
});

describe("MetricsCards: плитка «Возвраты (30 дн)»", () => {
  it("нет поля refunds (старый бэкенд) → нет плитки и нет оговорки про возвраты", async () => {
    renderCards();

    await screen.findByText(ru("adm.stats.payments_success"));
    expect(screen.queryByText(ru("adm.stats.refunds"))).toBeNull();
    expect(screen.queryByText(hasRefundsNote)).toBeNull();
  });

  it("валюты не складываются: 499 ₽ и $5 — не «504»", async () => {
    metrics = () =>
      Promise.resolve(
        base({
          refunds: {
            count_30d: 2,
            by_currency: [
              { currency: "RUB", count: 1, amount: 499 },
              { currency: "USD", count: 1, amount: 5 },
            ],
            reporting_gateways: ["VALUTIX"],
            silent_gateways: [],
          },
        }),
      );
    renderCards();

    const card = await tile(ru("adm.stats.refunds"));
    expect(within(card).getByText("499 ₽ · $5")).toBeInTheDocument();
    expect(within(card).getByText(ru("adm.stats.refunds_hint", { n: 2 }))).toBeInTheDocument();
    expect(card.textContent).not.toContain("504");
    // Оговорка про то, чего бот не видит, стоит в пояснении блока.
    expect(screen.getByText(hasRefundsNote)).toBeInTheDocument();
  });

  it("возвратов нет, но шлюз о них сообщает → «0 ₽» и список молчащих шлюзов", async () => {
    metrics = () =>
      Promise.resolve(
        base({
          refunds: {
            count_30d: 0,
            by_currency: [],
            reporting_gateways: ["VALUTIX"],
            silent_gateways: ["YOOMONEY"],
          },
        }),
      );
    renderCards();

    const card = await tile(ru("adm.stats.refunds"));
    expect(within(card).getByText("0 ₽")).toBeInTheDocument();
    expect(
      within(card).getByText(
        ru("adm.stats.refunds_hint_silent", { n: 0, gateways: "ЮMoney" }),
      ),
    ).toBeInTheDocument();
  });

  it("ни один подключённый шлюз о возвратах не сообщает → прочерк, а не ноль", async () => {
    metrics = () =>
      Promise.resolve(
        base({
          refunds: {
            count_30d: 0,
            by_currency: [],
            reporting_gateways: [],
            silent_gateways: ["TELEGRAM_STARS", "YOOMONEY"],
          },
        }),
      );
    renderCards();

    const card = await tile(ru("adm.stats.refunds"));
    expect(within(card).getByText("—")).toBeInTheDocument();
    expect(within(card).getByText(ru("adm.stats.refunds_silent_all"))).toBeInTheDocument();
    expect(card.textContent).not.toContain("0 ₽");
  });

  it("поверх «Бедолаги» (метрик нет, 501) → блок не рисуется и не падает", async () => {
    let reject!: (e: unknown) => void;
    metrics = () =>
      new Promise((_, rej) => {
        reject = rej;
      });
    renderCards();

    await act(async () => {
      reject(new ApiError(501, "Not implemented"));
    });
    expect(screen.queryByText(ru("adm.stats.kpi_title"))).toBeNull();
    expect(screen.queryByText(ru("adm.stats.refunds"))).toBeNull();
  });
});
