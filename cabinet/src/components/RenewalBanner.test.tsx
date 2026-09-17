import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RenewalBanner } from "./RenewalBanner";
import { I18nProvider } from "@/i18n/I18nContext";
import { detectInitialLang } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import type { SubscriptionInfoResponse } from "@/types/api";
import type { RenewalDiscountStatus } from "@/api/renewalDiscount";

// Подписка «через N дней от сегодня» — даты в фикстурах должны быть живыми,
// иначе тест начнёт врать через месяц.
function sub(over: Partial<SubscriptionInfoResponse> = {}): SubscriptionInfoResponse {
  const in30 = new Date(Date.now() + 30 * 86400_000).toISOString();
  return {
    user_remna_id: "1",
    status: "ACTIVE",
    is_trial: false,
    traffic_limit: 0,
    device_limit: 1,
    traffic_limit_strategy: "MONTH",
    expire_at: in30,
    url: "https://example.org/s/1",
    plan_name: "Стандартный",
    plan_duration_days: 30,
    used_traffic_bytes: 0,
    lifetime_used_traffic_bytes: null,
    online_at: null,
    ...over,
  };
}

// Язык провайдер выбирает сам (в jsdom это язык «браузера»), поэтому ждём
// текст ровно на том языке, на котором компонент его и нарисует.
const lang = detectInitialLang();
const say = (key: string, vars?: Record<string, string | number>) => translate(key, vars, lang);

function renderBanner(s: SubscriptionInfoResponse | null, offer?: RenewalDiscountStatus | null) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <RenewalBanner subscription={s} offer={offer} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  // Скрытие плашки живёт в sessionStorage — между тестами не должно протекать.
  sessionStorage.clear();
});

describe("RenewalBanner и пауза подписки", () => {
  it("DISABLED без признака паузы — по-прежнему «истекла» (наш бэкенд поля не шлёт)", () => {
    const { container } = renderBanner(sub({ status: "DISABLED" }));
    expect(container.textContent).toContain(say("renewal.expired"));
  });

  it("DISABLED + frozen — про истечение молчим и продлевать не зовём", () => {
    const { container } = renderBanner(sub({ status: "DISABLED", frozen: true }));
    expect(container.textContent).not.toContain(say("renewal.expired"));
    expect(container.querySelector('a[href="/billing"]')).toBeNull();
  });

  it("на паузе молчит и мягкое «скоро закончится» — срок ведь не идёт", () => {
    const soon = new Date(Date.now() + 36 * 3600_000).toISOString();
    const { container } = renderBanner(sub({ expire_at: soon, status: "DISABLED", frozen: true }));
    expect(container.textContent).toBe("");
  });

  it("frozen:false ничего не ломает — истёкшая подписка всё так же зовёт продлить", () => {
    const past = new Date(Date.now() - 86400_000).toISOString();
    const { container } = renderBanner(sub({ expire_at: past, status: "EXPIRED", frozen: false }));
    expect(container.textContent).toContain(say("renewal.expired"));
    expect(container.querySelector('a[href="/billing"]')).not.toBeNull();
  });

  it("исчерпанный трафик на паузе показываем — это про лимит, а не про срок", () => {
    const { container } = renderBanner(
      sub({ status: "DISABLED", frozen: true, traffic_limit: 10, used_traffic_bytes: 10 * 1024 ** 3 }),
    );
    expect(container.textContent).toContain(say("renewal.trafficOut"));
  });
});

describe("RenewalBanner и скидка на продление", () => {
  const inDays = (d: number) => new Date(Date.now() + d * 86400_000 + 3600_000).toISOString();
  const offer = (over: Partial<RenewalDiscountStatus> = {}): RenewalDiscountStatus => ({
    active: true,
    percent: 15,
    expires_at: new Date(Date.now() + 3 * 86400_000).toISOString(),
    ...over,
  });
  const offerTitle = say("renewalDiscount.title", { percent: 15 });

  it("скидка при «скоро закончится» — одна плашка с процентом вместо двух", () => {
    const { container } = renderBanner(sub({ expire_at: inDays(2) }), offer());
    expect(container.textContent).toContain(offerTitle);
    expect(container.textContent).not.toContain(say("renewal.inDays", { days: 2 }));
    expect(container.textContent).not.toContain(say("renewal.soonText"));
    expect(container.querySelectorAll('a[href="/billing"]')).toHaveLength(1);
  });

  it("скидку выдают за 5 дней — плашка есть, хотя «скоро закончится» ещё молчит", () => {
    const { container } = renderBanner(sub({ expire_at: inDays(5) }), offer());
    expect(container.textContent).toContain(offerTitle);
    expect(container.querySelector('a[href="/billing"]')).not.toBeNull();
  });

  it("подписка истекла — только «Подписка истекла», скидки нет", () => {
    const past = new Date(Date.now() - 86400_000).toISOString();
    const { container } = renderBanner(sub({ expire_at: past, status: "EXPIRED" }), offer());
    expect(container.textContent).toContain(say("renewal.expired"));
    expect(container.textContent).not.toContain(offerTitle);
  });

  it("без скидки всё как раньше", () => {
    const { container } = renderBanner(sub({ expire_at: inDays(2) }), null);
    expect(container.textContent).toContain(say("renewal.inDays", { days: 2 }));
    expect(container.textContent).not.toContain(offerTitle);
  });

  it("срок скидки вышел — её нет, возвращается обычное предупреждение", () => {
    const burnt = offer({ expires_at: new Date(Date.now() - 60_000).toISOString() });
    const { container } = renderBanner(sub({ expire_at: inDays(2) }), burnt);
    expect(container.textContent).not.toContain(offerTitle);
    expect(container.textContent).toContain(say("renewal.inDays", { days: 2 }));
  });

  it("скрытая скидка не возвращается мягким предупреждением в той же сессии", () => {
    const { container } = renderBanner(sub({ expire_at: inDays(2) }), offer());
    fireEvent.click(container.querySelector(`button[aria-label="${say("common.hide")}"]`)!);
    expect(container.textContent).toBe("");
  });
});
