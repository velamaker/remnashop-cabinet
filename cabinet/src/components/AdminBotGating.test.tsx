import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Appearance } from "@/api/appearance";

// Разделы админки, которым нужен новый бот. Кабинет бывает новее бота («только
// кабинет» в update.sh, кабинет на отдельном сервере): раньше пункт «Скидка до
// окончания» был в меню и на главной админки, а страница открывалась с «Не удалось
// загрузить». Здесь настоящие AuthProvider, AdminRoute, боковое меню и плитки — всё
// через один canPage; подменены только ответы бэкенда и оформление.

type Who = Record<string, unknown>;
let who: Who = {};
vi.mock("@/api/auth", () => ({
  authApi: {
    me: () => Promise.resolve({ id: 1, email: "owner@example.test" }),
    whoami: () => Promise.resolve(who),
  },
}));
vi.mock("@/api/referral", () => ({ referralApi: { attach: () => Promise.resolve() } }));
vi.mock("@/api/admin", () => ({
  updatesAdminApi: { version: () => Promise.resolve({ version: "0.0.0" }) },
}));
// Шапка админки тянет свои ручки и темы — к меню разделов они отношения не имеют.
vi.mock("@/components/ui/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }));
vi.mock("@/components/admin/Admin2FA", () => ({ Admin2FAUnlock: () => null }));
vi.mock("@/components/admin/AdminNotifBell", () => ({ AdminNotifBell: () => null }));

let branding: { appearance: Appearance | null; loaded: boolean; offline: boolean } = {
  appearance: null,
  loaded: false,
  offline: false,
};
vi.mock("@/contexts/BrandingContext", () => ({ useBranding: () => branding }));

const { AuthProvider } = await import("@/contexts/AuthContext");
const { AdminRoute } = await import("./AdminRoute");
const { AdminNavLauncher } = await import("./admin/AdminNavLauncher");

const OWNER: Who = { can_access_admin: true, is_owner: true, full_access: true, sections: [] };
const look = (over: Partial<Appearance> = {}) => ({ brand_name: "X", ...over }) as Appearance;
const oldBot = () => look();
const newBot = () => look({ bot_capabilities: ["renewal_discount"] });

function open(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/admin" element={<AdminRoute><AdminNavLauncher /></AdminRoute>} />
          <Route path="/admin/*" element={<AdminRoute><p>СТРАНИЦА РАЗДЕЛА</p></AdminRoute>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const discountLinks = () => document.querySelectorAll('a[href="/admin/renewal-discount"]');
const usersLinks = () => document.querySelectorAll('a[href="/admin/users"]');

beforeEach(() => {
  who = { ...OWNER };
  branding = { appearance: newBot(), loaded: true, offline: false };
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});
afterEach(() => cleanup());

describe("меню и плитки админки", () => {
  it("бот 1.3.8 — «Скидки до окончания» нет ни в меню, ни на главной; прочие разделы на месте", async () => {
    branding = { appearance: oldBot(), loaded: true, offline: false };
    open("/admin");
    // Меню и плитки: по ссылке в каждом.
    await waitFor(() => expect(usersLinks().length).toBe(2));
    expect(discountLinks().length).toBe(0);
  });

  it("бот с токеном — пункт и плитка есть", async () => {
    open("/admin");
    await waitFor(() => expect(discountLinks().length).toBe(2));
  });

  it("чужой бэкенд: решает его список страниц, а не токены нашего бота", async () => {
    branding = { appearance: look({ features: {} }), loaded: true, offline: false };
    who = { ...OWNER, pages: ["/admin", "/admin/users"] };
    const { unmount } = open("/admin");
    await waitFor(() => expect(usersLinks().length).toBe(2));
    expect(discountLinks().length).toBe(0);
    unmount();

    who = { ...OWNER, pages: ["/admin", "/admin/users", "/admin/renewal-discount"] };
    open("/admin");
    await waitFor(() => expect(discountLinks().length).toBe(2));
  });
});

describe("прямой заход по адресу", () => {
  it("бот 1.3.8 — «Раздела здесь нет» с подсказкой обновить бота, сама страница не рисуется", async () => {
    branding = { appearance: oldBot(), loaded: true, offline: false };
    open("/admin/renewal-discount");
    await screen.findByText("Раздела здесь нет");
    expect(screen.getByText(/после обновления бота/).textContent).toContain("./update.sh --with-bot");
    expect(screen.queryByText("СТРАНИЦА РАЗДЕЛА")).toBeNull();
  });

  it("вложенный адрес спрятанной страницы — тоже нет", async () => {
    branding = { appearance: oldBot(), loaded: true, offline: false };
    open("/admin/renewal-discount/stats");
    await screen.findByText("Раздела здесь нет");
    expect(screen.queryByText("СТРАНИЦА РАЗДЕЛА")).toBeNull();
  });

  it("оформление ещё не пришло с сервера — загрузчик, а не «раздела нет»", async () => {
    // Кэш есть (appearance не null), но списка возможностей в кэше не бывает.
    branding = { appearance: oldBot(), loaded: false, offline: false };
    open("/admin/renewal-discount");
    await waitFor(() => expect(document.querySelector(".animate-spin")).not.toBeNull());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Раздела здесь нет")).toBeNull();
    expect(screen.queryByText("СТРАНИЦА РАЗДЕЛА")).toBeNull();
  });

  it("бот с токеном — страница открывается; разделы вне манифеста не ждут оформления", async () => {
    const { unmount } = open("/admin/renewal-discount");
    await screen.findByText("СТРАНИЦА РАЗДЕЛА");
    unmount();

    branding = { appearance: null, loaded: false, offline: false };
    open("/admin/users");
    await screen.findByText("СТРАНИЦА РАЗДЕЛА");
  });

  it("причина от чужого бэкенда главнее своей", async () => {
    branding = { appearance: look({ features: {} }), loaded: true, offline: false };
    who = {
      ...OWNER,
      pages: ["/admin"],
      page_notes: { "/admin/renewal-discount": "У этого бота скидок до окончания нет." },
    };
    open("/admin/renewal-discount");
    await screen.findByText("У этого бота скидок до окончания нет.");
    expect(screen.queryByText(/после обновления бота/)).toBeNull();
  });
});
