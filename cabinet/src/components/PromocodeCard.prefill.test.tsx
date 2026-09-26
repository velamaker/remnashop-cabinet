import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";

/**
 * Код из ссылки сертификата подставляется в поле промокода.
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. Кнопка «Активировать в кабинете» ведёт на
 * `/subscription?promo=GIFT-…`: код должен оказаться в поле, человек — увидеть
 * подсказку, а активация — остаться за ним (подарок может сменить тариф, и такое
 * решение не принимают за человека при открытии страницы).
 */

const activate = vi.fn();
vi.mock("@/api/promocode", () => ({ promocodeApi: { activate: (c: string) => activate(c) } }));
vi.mock("@/contexts/BrandingContext", () => ({ useBranding: () => ({ can: () => true }) }));

const { PromocodeCard } = await import("./PromocodeCard");
const ru = (key: string) => translate(key, undefined, "ru");
const CODE = "GIFT-" + "A".repeat(32);

function openAt(search: string) {
  window.history.replaceState({}, "", `/subscription${search}`);
  render(
    <I18nProvider>
      <PromocodeCard />
    </I18nProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  activate.mockReset();
});
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

describe("промокод из ссылки сертификата", () => {
  it("код подставлен, подсказка видна, активации без нажатия нет", () => {
    openAt(`?promo=${CODE.toLowerCase()}`);
    expect((screen.getByPlaceholderText(ru("promo.placeholder")) as HTMLInputElement).value).toBe(CODE);
    expect(screen.getByText(ru("promo.fromGift"))).toBeTruthy();
    expect(activate).not.toHaveBeenCalled();
  });

  it("чужой промокод по ссылке не подставляем", () => {
    // Иначе ссылка на наш домен с плашкой «код подарка» подсовывала бы любой код —
    // например, меньшую персональную скидку, которая затёрла бы большую.
    openAt("?promo=SALE5");
    expect((screen.getByPlaceholderText(ru("promo.placeholder")) as HTMLInputElement).value).toBe("");
    expect(screen.queryByText(ru("promo.fromGift"))).toBeNull();
  });

  it("после успешной активации код уходит из адреса — повторно не подставится", async () => {
    activate.mockResolvedValue({ reward_type: "SUBSCRIPTION", reward: null });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    openAt(`?promo=${CODE}&x=1`);
    fireEvent.click(screen.getByText(ru("promo.apply")));
    await waitFor(() => expect(activate).toHaveBeenCalledWith(CODE));
    await waitFor(() => expect(window.location.search).toBe("?x=1"));
    vi.useRealTimers();
  });

  it("без параметра — пустое поле и никакой подсказки", () => {
    openAt("");
    expect((screen.getByPlaceholderText(ru("promo.placeholder")) as HTMLInputElement).value).toBe("");
    expect(screen.queryByText(ru("promo.fromGift"))).toBeNull();
  });
});
