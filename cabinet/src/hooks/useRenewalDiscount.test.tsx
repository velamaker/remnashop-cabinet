import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { ApiError } from "@/types/api";

// Скидка на продление — необязательное дополнение к плашке. Любой отказ бэкенда
// (чужой бот без ручки, сеть) обязан превращаться в «скидки нет», а не в ошибку
// на Главной.
const get = vi.fn();
vi.mock("@/api/renewalDiscount", () => ({ renewalDiscountApi: { get: () => get() } }));

const { useRenewalDiscount } = await import("./useRenewalDiscount");

// Тело — блоком, не выражением: mockReset() возвращает сам шпион, а функцию,
// возвращённую из beforeEach, vitest вызывает после теста как уборку — и
// отклонённый промис из неё ронял бы тест.
beforeEach(() => {
  get.mockReset();
});

describe("useRenewalDiscount", () => {
  it.each([
    ["501", () => new ApiError(501, "Этот раздел недоступен на этом сервисе")],
    ["404", () => new ApiError(404, "Not Found")],
    ["сеть", () => new TypeError("Failed to fetch")],
  ])("%s → null", async (_name, failure) => {
    get.mockImplementation(() => Promise.reject(failure()));
    const { result } = renderHook(() => useRenewalDiscount(true));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(result.current).toBeNull();
  });

  it("активная скидка приходит как есть, неактивная — null", async () => {
    get.mockResolvedValueOnce({ active: true, percent: 10, expires_at: "2026-10-01T00:00:00Z" });
    const { result } = renderHook(() => useRenewalDiscount(true));
    await waitFor(() => expect(result.current?.percent).toBe(10));

    get.mockResolvedValueOnce({ active: false });
    const second = renderHook(() => useRenewalDiscount(true));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 0));
    expect(second.result.current).toBeNull();
  });

  it("выключен — не спрашивает бэкенд вовсе", async () => {
    const { result } = renderHook(() => useRenewalDiscount(false));
    await new Promise((r) => setTimeout(r, 0));
    expect(get).not.toHaveBeenCalled();
    expect(result.current).toBeNull();
  });
});
