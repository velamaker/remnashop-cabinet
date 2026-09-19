import { describe, expect, it } from "vitest";
import { renewPreselect } from "./planChange";
import type { SubscriptionOffersResponse } from "@/types/api";

/**
 * Ссылка «Продлить» из сообщения бота ведёт на витрину с меткой `renew=1`, а какой
 * тариф раскрыть — решает сама витрина. Так ссылка не протухает при смене тарифа и
 * не зависит от того, что бот успел прочитать из базы.
 *
 * ЧТО ЗАПЕРТО: берём именно тариф для ПРОДЛЕНИЯ, срок — тот же, что у человека сейчас,
 * а если такого срока уже не продают — первый доступный. Нечего продлевать (новый
 * человек) — ничего не раскрываем и не притворяемся, что выбрали.
 */

function offers(over: Partial<SubscriptionOffersResponse> = {}): SubscriptionOffersResponse {
  return {
    gateways: [],
    plans: [
      {
        public_code: "solo",
        recommended_purchase_type: "CHANGE",
        durations: [{ days: 30 }, { days: 90 }],
      },
      {
        public_code: "home",
        recommended_purchase_type: "RENEW",
        durations: [{ days: 30 }, { days: 90 }, { days: 180 }],
      },
    ],
    has_current_subscription: true,
    current_subscription_status: "ACTIVE",
    ...over,
  } as unknown as SubscriptionOffersResponse;
}

describe("что раскрыть по ссылке «Продлить»", () => {
  it("берёт тариф, который и продлевается", () => {
    expect(renewPreselect(offers()).code).toBe("home");
  });

  it("срок — тот же, что у человека сейчас", () => {
    expect(renewPreselect(offers({ current_days_left: 90 })).days).toBe(90);
  });

  it("такого срока больше нет в продаже — берём первый доступный", () => {
    expect(renewPreselect(offers({ current_days_left: 45 })).days).toBe(30);
  });

  it("продлевать нечего — ничего не выбираем", () => {
    const fresh = offers({
      plans: [
        { public_code: "solo", recommended_purchase_type: "NEW", durations: [{ days: 30 }] },
      ] as never,
    });
    expect(renewPreselect(fresh)).toEqual({ code: null, days: null });
  });
});
