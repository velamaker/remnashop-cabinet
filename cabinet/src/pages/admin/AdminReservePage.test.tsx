import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { ApiError } from "@/types/api";

// Блок «Кто на резерве» существует ради одного: увидеть выданный резерв, которым
// НЕЛЬЗЯ пользоваться. Резерв даёт серверы через сквады, поэтому ACTIVE без сквадов —
// это «выдан», а в приложении пусто; именно так дефект и прятался, пока состояние
// панели не показывали. Тесты держат ровно это поведение.
let grants: () => Promise<unknown> = () => Promise.resolve({ items: [], active: 0, broken: 0 });

vi.mock("@/api/admin", () => ({
  reserveAdminApi: {
    // Карточка настроек не предмет этих тестов — отдаём минимум, чтобы она отрисовалась.
    get: () => Promise.resolve({ enabled: true, reserve_gb: 1, window_days: 7, squad_uuid: "" }),
    update: (data: unknown) => Promise.resolve(data),
    grants: () => grants(),
  },
}));

const { default: AdminReservePage } = await import("./AdminReservePage");

const card = () => screen.queryByText("Кто на резерве");

const grant = (over: Record<string, unknown> = {}) => ({
  id: 1,
  user_id: 42,
  telegram_id: 777,
  username: "vasya",
  remna_uuid: "11111111-2222-3333-4444-555555555555",
  granted_at: "2026-08-20T10:00:00Z",
  reserve_expire_at: "2026-08-27T10:00:00Z",
  ended: false,
  panel: { status: "ACTIVE", squads: ["Reserve-1GB"], traffic_limit_gb: 1, used_traffic_gb: 0.2 },
  problem: null,
  note: null,
  ...over,
});

beforeEach(() => {
  grants = () => Promise.resolve({ items: [], active: 0, broken: 0 });
});
afterEach(cleanup);

describe("Кто на резерве", () => {
  it("рабочий резерв: показан сквад и расход, предупреждения нет", async () => {
    grants = () => Promise.resolve({ items: [grant()], active: 1, broken: 0 });
    render(<AdminReservePage />);

    await waitFor(() => expect(card()).not.toBeNull());
    expect(screen.queryByText("vasya")).not.toBeNull();
    expect(screen.queryByText("Reserve-1GB")).not.toBeNull();
    expect(screen.queryByText("0.2 / 1 ГБ")).not.toBeNull();
    expect(screen.queryByText(/не работает/)).toBeNull();
  });

  it("выдан, но сквадов нет → строка помечена проблемой и счётчик её считает", async () => {
    grants = () =>
      Promise.resolve({
        items: [grant({ panel: { status: "ACTIVE", squads: [], traffic_limit_gb: 1, used_traffic_gb: 0 },
                        problem: "нет активных сквадов — в приложении будет пусто" })],
        active: 1,
        broken: 1,
      });
    render(<AdminReservePage />);

    await waitFor(() => expect(card()).not.toBeNull());
    expect(screen.queryByText("нет активных сквадов — в приложении будет пусто")).not.toBeNull();
    expect(screen.queryByText(/не работает/)).not.toBeNull();
  });

  it("израсходованный гигабайт — обычная пометка, а не предупреждение", async () => {
    grants = () =>
      Promise.resolve({
        items: [grant({ panel: { status: "LIMITED", squads: ["Reserve-1GB"], traffic_limit_gb: 1, used_traffic_gb: 1 },
                        note: "резерв израсходован" })],
        active: 1,
        broken: 0,
      });
    render(<AdminReservePage />);

    await waitFor(() => expect(card()).not.toBeNull());
    expect(screen.queryByText("резерв израсходован")).not.toBeNull();
    expect(screen.queryByText(/не работает/)).toBeNull();
  });

  it("сломанные строки идут выше здоровых", async () => {
    grants = () =>
      Promise.resolve({
        items: [
          grant({ id: 1, username: "здоровый" }),
          grant({ id: 2, username: "сломанный", problem: "нет активных сквадов" }),
        ],
        active: 2,
        broken: 1,
      });
    render(<AdminReservePage />);

    await waitFor(() => expect(card()).not.toBeNull());
    const names = screen.getAllByText(/здоровый|сломанный/).map((n) => n.textContent);
    expect(names[0]).toBe("сломанный");
  });

  it("несколько выдач одному человеку (резерв на каждое истечение) рисуются обе", async () => {
    grants = () =>
      Promise.resolve({
        items: [
          grant({ id: 2, ended: false }),
          grant({ id: 1, ended: true, panel: null }),
        ],
        active: 1,
        broken: 0,
      });
    render(<AdminReservePage />);

    await waitFor(() => expect(card()).not.toBeNull());
    expect(screen.getAllByText("vasya")).toHaveLength(2);
    expect(screen.queryByText("закончился")).not.toBeNull();
  });

  it("адаптер «Бедолаги»: ручки нет (501) → блока нет, страница цела", async () => {
    grants = () => Promise.reject(new ApiError(501, "Адаптер пока не умеет"));
    render(<AdminReservePage />);

    await waitFor(() => expect(screen.queryByText("Резервный доступ истёкшим")).not.toBeNull());
    expect(card()).toBeNull();
  });

  it("настоящая ошибка бэкенда не прячется, в отличие от 501", async () => {
    grants = () => Promise.reject(new ApiError(500, "Всё сломалось"));
    render(<AdminReservePage />);

    await waitFor(() => expect(screen.queryByText("Всё сломалось")).not.toBeNull());
  });

  it("резерв ещё никому не выдавали → таблицы нет, но блок на месте", async () => {
    render(<AdminReservePage />);

    await waitFor(() => expect(card()).not.toBeNull());
    expect(screen.queryByText("Резерв пока никому не выдавался.")).not.toBeNull();
  });
});
