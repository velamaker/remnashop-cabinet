import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";

/**
 * Порядок способов оплаты.
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. Первый шлюз в списке — это способ, который человек видит
 * первым и которым платит, не выбирая. Задавала его миграция базового образа, где
 * первым стоит Telegram Stars; у нас он за полгода не принёс ни рубля, а ЮMoney
 * принёс всю выручку — и переставить их владелец мог только руками в базе.
 * Поэтому проверяем не «кнопка нажалась», а три вещи, из-за которых это ломается
 * незаметно: уезжает ли на сервер ПОЛНЫЙ порядок, видно ли перестановку сразу,
 * и возвращается ли список к правде, если сервер отказал.
 */

const list = vi.fn();
const reorder = vi.fn();

vi.mock("@/api/admin", () => ({
  gatewaysAdminApi: {
    list: () => list(),
    reorder: (ids: number[]) => reorder(ids),
    toggle: vi.fn(),
    fields: vi.fn(),
    setField: vi.fn(),
    test: vi.fn(),
  },
}));

const { default: AdminGatewaysPage } = await import("./AdminGatewaysPage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

function gateway(id: number, type: string, order: number) {
  return {
    id,
    type,
    currency: type === "TELEGRAM_STARS" ? "XTR" : "RUB",
    is_active: true,
    is_configured: true,
    order_index: order,
    display_name: null,
  };
}

const STARS_FIRST = [gateway(1, "TELEGRAM_STARS", 1), gateway(3, "YOOMONEY", 2)];
const MONEY_FIRST = [gateway(3, "YOOMONEY", 1), gateway(1, "TELEGRAM_STARS", 2)];

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  list.mockResolvedValue({ items: STARS_FIRST, total: 2 });
  reorder.mockReset();
  reorder.mockResolvedValue({ items: MONEY_FIRST, total: 2 });
});
afterEach(() => cleanup());

async function open() {
  render(
    <I18nProvider>
      <AdminGatewaysPage />
    </I18nProvider>,
  );
  await waitFor(() => expect(screen.getAllByTitle(ru("adm.gateways.move_up"))).toHaveLength(2));
}

/** Названия карточек в том порядке, в каком они на экране. */
function order(): string[] {
  return screen
    .getAllByTitle(ru("adm.gateways.move_up"))
    .map((btn) => btn.closest("div.rounded-2xl")!.querySelector("p.font-semibold")!.textContent!.trim());
}

describe("порядок способов оплаты", () => {
  it("первый в списке помечен как «по умолчанию»", async () => {
    await open();
    expect(order()[0]).toContain(ru("adm.gateways.first"));
    expect(order()[1]).not.toContain(ru("adm.gateways.first"));
  });

  it("«ниже» у первого отправляет полный порядок и сразу меняет экран", async () => {
    await open();
    fireEvent.click(screen.getAllByTitle(ru("adm.gateways.move_down"))[0]!);

    // Порядок уезжает целиком: частичный список бэкенд не принимает, а «дотасовать»
    // его на сервере — значит разойтись с тем, что админ видел.
    await waitFor(() => expect(reorder).toHaveBeenCalledWith([3, 1]));
    await waitFor(() => expect(order()[0]).toContain("ЮMoney"));
  });

  it("выше некуда и ниже некуда — стрелки на краях заперты", async () => {
    await open();
    const up = screen.getAllByTitle(ru("adm.gateways.move_up"));
    const down = screen.getAllByTitle(ru("adm.gateways.move_down"));
    expect(up[0]!.hasAttribute("disabled")).toBe(true);
    expect(down[down.length - 1]!.hasAttribute("disabled")).toBe(true);
    expect(down[0]!.hasAttribute("disabled")).toBe(false);
  });

  it("сервер отказал — показываем то, что в базе, а не свою перестановку", async () => {
    reorder.mockRejectedValue(new Error("нет связи"));
    await open();
    fireEvent.click(screen.getAllByTitle(ru("adm.gateways.move_down"))[0]!);

    await screen.findByText(ru("adm.gateways.order_failed"));
    await waitFor(() => expect(order()[0]).toContain("Telegram Stars"));
  });
});
