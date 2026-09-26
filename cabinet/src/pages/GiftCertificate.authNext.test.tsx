import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";

/**
 * Код подарка переживает вход и регистрацию — любым способом.
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. Гость открывает ссылку-сертификат, жмёт «в кабинете» или
 * «Нет аккаунта» и по дороге входит или регистрируется. Код едет в ?next= и
 * обязан вернуться в поле промокода (/billing?promo=…), какой бы кнопкой человек
 * ни воспользовался. Раньше он терялся на трёх путях:
 *   * кнопка OIDC уводила на /api/auth/telegram/oidc/start без next, а сервер
 *     после входа всегда вёл на главную;
 *   * ссылки «Зарегистрироваться» (со входа) и «Войти» (с регистрации) не несли next;
 *   * вход виджетом на странице регистрации делал navigate("/") и перебивал
 *     переход PublicOnlyRoute на next.
 * Каждый путь проходится целиком, начиная со страницы сертификата.
 */

const CODE = "GIFT-" + "A".repeat(32);
const TARGET = `/billing?promo=${CODE}`;
const ORIGIN = "https://cabinet.example";

// Состояние входа. Как в настоящем AuthContext, пользователь появляется ДО того,
// как промис входа завершится (там после /auth/me ещё идёт /auth/whoami). Поэтому
// PublicOnlyRoute успевает увести на next раньше, чем сработает переход в
// обработчике кнопки, — и именно этот поздний переход решает, где окажется человек.
const auth = vi.hoisted(() => {
  let user: { id: number } | null = null;
  const subs = new Set<() => void>();
  return {
    pending: Promise.resolve(),
    get: () => user,
    set(next: { id: number } | null) {
      user = next;
      subs.forEach((fn) => fn());
    },
    subscribe(fn: () => void) {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
  };
});

vi.mock("@/contexts/AuthContext", async () => {
  const { useSyncExternalStore } = await import("react");
  const signIn = () => {
    auth.pending = (async () => {
      auth.set({ id: 1 });
      await new Promise((resolve) => setTimeout(resolve, 20));
    })();
    return auth.pending;
  };
  return {
    useAuth: () => ({
      user: useSyncExternalStore(auth.subscribe, auth.get),
      isLoading: false,
      login: signIn,
      register: signIn,
      loginWithTelegram: signIn,
      loginWithTelegramWebApp: signIn,
    }),
  };
});

const branding = vi.hoisted(() => ({ oidc: false }));
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({
    appearance: null,
    emailAuthEnabled: true,
    telegramOidcEnabled: branding.oidc,
  }),
}));

vi.mock("@/api/gift", () => ({
  giftApi: {
    certificate: () =>
      Promise.resolve({ code: CODE, plan_name: "DUO", days: 30, state: "ready", bot_url: null }),
  },
}));

// Виджет Telegram — внешний скрипт; вместо него кнопка, отдающая «подписанные» данные.
vi.mock("@/components/TelegramLoginButton", () => ({
  TelegramLoginButton: ({ onAuth }: { onAuth: (d: unknown) => void }) => (
    <button type="button" onClick={() => onAuth({ id: 7, first_name: "T", auth_date: 1, hash: "h" })}>
      tg-widget
    </button>
  ),
}));
// Оформление, меню кабинета и согласие с документами к адресу возврата отношения не имеют.
vi.mock("@/components/layout/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/LegalConsent", () => ({ LegalConsent: () => null }));
vi.mock("@/components/TelegramConsentRetry", () => ({ TelegramConsentRetry: () => null }));
vi.mock("@/components/BrandLogo", () => ({ BrandLogo: () => null }));
vi.mock("@/components/BrandWordmark", () => ({ BrandWordmark: () => null }));
vi.mock("@/components/LanguageSwitcher", () => ({ LanguageSwitcher: () => null }));
vi.mock("@/components/ui/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }));

// Имя бота вшивается в бандл при сборке и читается при загрузке модуля — задаём
// его до импорта страниц, иначе виджета на экране не будет вовсе.
vi.stubEnv("VITE_TELEGRAM_BOT_USERNAME", "test_bot");
const { default: GiftCertificatePage } = await import("./GiftCertificatePage");
const { default: LoginPage } = await import("./LoginPage");
const { default: RegisterPage } = await import("./RegisterPage");
const { ProtectedRoute, PublicOnlyRoute } = await import("@/components/ProtectedRoute");

const ru = (key: string) => translate(key, undefined, "ru");

function Where() {
  const loc = useLocation();
  return <p data-testid="where">{loc.pathname + loc.search}</p>;
}

const where = () => screen.getByTestId("where").textContent;

function openCertificate() {
  return render(
    <MemoryRouter initialEntries={[`/gift/${CODE}`]}>
      <I18nProvider>
        <Routes>
          <Route path="/gift/:code" element={<GiftCertificatePage />} />
          <Route path="/login" element={<PublicOnlyRoute><LoginPage /></PublicOnlyRoute>} />
          <Route path="/register" element={<PublicOnlyRoute><RegisterPage /></PublicOnlyRoute>} />
          <Route path="/billing" element={<ProtectedRoute><Where /></ProtectedRoute>} />
          <Route path="*" element={<Where />} />
        </Routes>
      </I18nProvider>
    </MemoryRouter>,
  );
}

/** Дожидается конца входа — вместе с поздним переходом из обработчика кнопки. */
async function signInFinished() {
  await act(async () => {
    await auth.pending;
  });
}

function fillAndSubmit(container: HTMLElement) {
  fireEvent.change(container.querySelector('input[type="email"]')!, { target: { value: "new@example.test" } });
  fireEvent.change(container.querySelector('input[type="password"]')!, { target: { value: "long-enough-pass" } });
  fireEvent.submit(container.querySelector("form")!);
}

async function toRegisterFromCertificate() {
  fireEvent.click(await screen.findByRole("link", { name: ru("gift.cert.noAccount") }));
  // На регистрации — пока ещё с кодом в next.
  await screen.findByRole("button", { name: ru("register.submit") });
}

async function toLoginFromCertificate() {
  fireEvent.click(await screen.findByRole("link", { name: ru("gift.cert.inCabinet") }));
  // ProtectedRoute не пустил гостя и отправил на вход, сохранив адрес в next.
  await screen.findByRole("button", { name: ru("login.submit") });
}

// Переход на OIDC — уход браузера на сервер: location.href перехватываем.
let assigned: string | null = null;
const realLocation = Object.getOwnPropertyDescriptor(window, "location");

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  auth.set(null);
  auth.pending = Promise.resolve();
  branding.oidc = false;
  assigned = null;
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      origin: ORIGIN,
      get href() {
        return `${ORIGIN}/`;
      },
      set href(value: string) {
        assigned = value;
      },
    },
  });
});

afterEach(() => {
  cleanup();
  if (realLocation) Object.defineProperty(window, "location", realLocation);
});

describe("сертификат → «Нет аккаунта» → регистрация", () => {
  it("почтой — возвращает в поле промокода", async () => {
    const { container } = openCertificate();
    await toRegisterFromCertificate();
    fillAndSubmit(container);
    await signInFinished();
    await waitFor(() => expect(where()).toBe(TARGET));
  });

  it("виджетом Telegram — возвращает в поле промокода, а не на главную", async () => {
    openCertificate();
    await toRegisterFromCertificate();
    fireEvent.click(screen.getByRole("button", { name: "tg-widget" }));
    await signInFinished();
    expect(where()).toBe(TARGET);
  });

  it("кнопкой OIDC — next уходит на сервер вместе со входом", async () => {
    branding.oidc = true;
    openCertificate();
    await toRegisterFromCertificate();
    fireEvent.click(screen.getByRole("button", { name: ru("login.viaTelegram") }));
    expect(assigned).toBe(`/api/auth/telegram/oidc/start?next=${encodeURIComponent(TARGET)}`);
  });

  it("передумал и нажал «Войти» — вход почтой возвращает туда же", async () => {
    const { container } = openCertificate();
    await toRegisterFromCertificate();
    fireEvent.click(screen.getByRole("link", { name: ru("login.submit") }));
    await screen.findByRole("button", { name: ru("login.submit") });
    fillAndSubmit(container);
    await signInFinished();
    await waitFor(() => expect(where()).toBe(TARGET));
  });
});

describe("сертификат → «в кабинете» → вход", () => {
  it("почтой — возвращает в поле промокода", async () => {
    const { container } = openCertificate();
    await toLoginFromCertificate();
    fillAndSubmit(container);
    await signInFinished();
    await waitFor(() => expect(where()).toBe(TARGET));
  });

  it("виджетом Telegram — возвращает в поле промокода", async () => {
    openCertificate();
    await toLoginFromCertificate();
    fireEvent.click(screen.getByRole("button", { name: "tg-widget" }));
    await signInFinished();
    expect(where()).toBe(TARGET);
  });

  it("кнопкой OIDC — next уходит на сервер вместе со входом", async () => {
    branding.oidc = true;
    openCertificate();
    await toLoginFromCertificate();
    fireEvent.click(screen.getByRole("button", { name: ru("login.viaTelegram") }));
    expect(assigned).toBe(`/api/auth/telegram/oidc/start?next=${encodeURIComponent(TARGET)}`);
  });

  it("аккаунта нет — «Зарегистрироваться» и регистрация почтой возвращают туда же", async () => {
    const { container } = openCertificate();
    await toLoginFromCertificate();
    fireEvent.click(screen.getByRole("link", { name: ru("login.register") }));
    await screen.findByRole("button", { name: ru("register.submit") });
    fillAndSubmit(container);
    await signInFinished();
    await waitFor(() => expect(where()).toBe(TARGET));
  });
});

describe("без next — всё как раньше", () => {
  it("OIDC со страницы входа без next уходит на голый адрес", async () => {
    branding.oidc = true;
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <I18nProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </I18nProvider>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: ru("login.viaTelegram") }));
    expect(assigned).toBe("/api/auth/telegram/oidc/start");
  });

  it("чужой адрес в next до сервера не доезжает", async () => {
    branding.oidc = true;
    render(
      <MemoryRouter initialEntries={[`/register?next=${encodeURIComponent("/\t/evil.example")}`]}>
        <I18nProvider>
          <Routes>
            <Route path="/register" element={<RegisterPage />} />
          </Routes>
        </I18nProvider>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: ru("login.viaTelegram") }));
    expect(assigned).toBe("/api/auth/telegram/oidc/start");
  });
});
