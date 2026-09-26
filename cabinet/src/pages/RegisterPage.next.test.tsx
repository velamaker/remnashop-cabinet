import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";

/**
 * Регистрация возвращает туда, откуда прислали (?next=…).
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. Новичок со ссылки-сертификата идёт «зарегистрироваться» с
 * next=/billing?promo=GIFT-…. Раньше после регистрации всегда был переход на
 * главную, и код подарка терялся на первой же странице. И обратная сторона:
 * next с чужим адресом не должен уводить с сайта.
 */

const register = vi.fn().mockResolvedValue(undefined);
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ register }) }));
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ appearance: null, emailAuthEnabled: true }),
}));
// Оформление и вход через Telegram к переходу после регистрации отношения не имеют.
vi.mock("@/components/LegalConsent", () => ({ LegalConsent: () => null }));
vi.mock("@/components/TelegramAuthBlock", () => ({ TelegramAuthBlock: () => null }));
vi.mock("@/components/BrandLogo", () => ({ BrandLogo: () => null }));
vi.mock("@/components/BrandWordmark", () => ({ BrandWordmark: () => null }));
vi.mock("@/components/LanguageSwitcher", () => ({ LanguageSwitcher: () => null }));
vi.mock("@/components/ui/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }));

const { default: RegisterPage } = await import("./RegisterPage");

function Where() {
  const loc = useLocation();
  return <p data-testid="where">{loc.pathname + loc.search}</p>;
}

function registerAt(url: string) {
  const { container } = render(
    <MemoryRouter initialEntries={[url]}>
      <I18nProvider>
        <Routes>
          <Route path="/register" element={<RegisterPage />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </I18nProvider>
    </MemoryRouter>,
  );
  fireEvent.change(container.querySelector('input[type="email"]')!, { target: { value: "new@example.test" } });
  fireEvent.change(container.querySelector('input[type="password"]')!, { target: { value: "long-enough-pass" } });
  fireEvent.submit(container.querySelector("form")!);
}

afterEach(() => cleanup());

describe("после регистрации", () => {
  it("возвращает на next — код подарка доходит до поля промокода", async () => {
    const next = "/billing?promo=GIFT-" + "A".repeat(32);
    registerAt(`/register?next=${encodeURIComponent(next)}`);
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe(next));
  });

  it("без next — на главную, как раньше", async () => {
    registerAt("/register");
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/"));
  });

  it("чужой адрес в next не уводит с сайта", async () => {
    registerAt(`/register?next=${encodeURIComponent("//evil.example/steal")}`);
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/"));
  });
});
