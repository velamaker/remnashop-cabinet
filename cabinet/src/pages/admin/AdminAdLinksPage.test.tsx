import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/types/api";
import type { Appearance } from "@/api/appearance";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

// Окно создания рекламной ссылки. Держим ровно то, на чём спотыкались люди:
// серые образцы принимали за введённый текст, код с пробелом или кириллицей
// сохранялся и молча не считал переходы, а готовую ссылку приходилось искать.
const create = vi.fn();

vi.mock("@/api/admin", () => ({
  adLinksAdminApi: {
    list: () => Promise.resolve({ items: [], total: 0 }),
    create: (data: unknown) => create(data),
    stats: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

// Какой бот под кабинетом — задаёт тест: готовую ссылку отдаёт только бот с токеном
// ad_link_url, и от этого зависит пояснение под кодом.
let appearance: Appearance | null = { brand_name: "X", bot_capabilities: ["ad_link_url"] } as Appearance;
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ appearance }),
}));

const { default: AdminAdLinksPage } = await import("./AdminAdLinksPage");

// Подписи страница больше не хранит в коде: они приходят из словаря по ключам
// adm.adlinks.*. Тест сверяется с тем же словарём (и держит админку на русском),
// иначе он проверял бы не интерфейс, а копию строки.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const rx = (key: string, vars?: Record<string, string | number>) =>
  new RegExp(ru(key, vars).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

async function openModal() {
  render(
    <I18nProvider>
      <AdminAdLinksPage />
    </I18nProvider>,
  );
  await screen.findByText(ru("adm.adlinks.empty"));
  fireEvent.click(screen.getByRole("button", { name: rx("adm.adlinks.create") }));
  return screen.getByText(ru("adm.adlinks.create_title")).closest("div.w-full") as HTMLElement;
}

const nameInput = () => screen.getByLabelText(rx("adm.adlinks.name_label")) as HTMLInputElement;
const codeInput = () => screen.getByLabelText(rx("adm.adlinks.code_label")) as HTMLInputElement;
const submit = (modal: HTMLElement) =>
  within(modal).getByRole("button", { name: ru("adm.adlinks.create") });

describe("окно создания рекламной ссылки", () => {
  beforeEach(() => {
    localStorage.setItem(STORAGE_KEY, "ru");
    setActiveLang("ru");
    create.mockReset();
    appearance = { brand_name: "X", bot_capabilities: ["ad_link_url"] } as Appearance;
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
  });
  afterEach(() => cleanup());

  it("поля пустые, а подсказки — это образцы из словаря, а не введённый текст", async () => {
    await openModal();
    expect(nameInput().value).toBe("");
    expect(codeInput().value).toBe("");
    expect(nameInput().placeholder).toBe(ru("adm.adlinks.name_placeholder"));
    expect(codeInput().placeholder).toBe(ru("adm.adlinks.code_placeholder"));
  });

  it("код подставляется из названия, пока его не поправили руками", async () => {
    await openModal();
    fireEvent.change(nameInput(), { target: { value: "Сторис у блогера" } });
    expect(codeInput().value).toBe("storis_u_blogera");

    fireEvent.change(codeInput(), { target: { value: "my_code" } });
    fireEvent.change(nameInput(), { target: { value: "Другое название" } });
    expect(codeInput().value).toBe("my_code");
  });

  it("недопустимый код объясняется сразу и не отправляется", async () => {
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "Реклама" } });
    fireEvent.change(codeInput(), { target: { value: "сторис июнь" } });

    expect(within(modal).getByText(ru("adm.adlinks.code_bad_chars"))).toBeTruthy();
    expect((submit(modal) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(submit(modal).closest("form")!);
    expect(create).not.toHaveBeenCalled();
  });

  it("после создания показывает готовую ссылку и копирует именно её", async () => {
    create.mockResolvedValue({
      id: 7, name: "Сторис у блогера", code: "storis_u_blogera", is_active: true, created_at: null,
      url: "https://t.me/example_bot?start=ad_storis_u_blogera",
    });
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "  Сторис у блогера " } });
    fireEvent.click(submit(modal));

    await screen.findByText(ru("adm.adlinks.created_title"));
    expect(create).toHaveBeenCalledWith({ name: "Сторис у блогера", code: "storis_u_blogera" });
    expect(screen.getByText("https://t.me/example_bot?start=ad_storis_u_blogera")).toBeTruthy();

    fireEvent.click(screen.getByTitle(ru("adm.adlinks.copy_link")));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("https://t.me/example_bot?start=ad_storis_u_blogera");
  });

  it("без адреса бота честно показывает код с пояснением", async () => {
    create.mockResolvedValue({ id: 8, name: "Пост", code: "post", is_active: true, created_at: null });
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "Пост" } });
    fireEvent.click(submit(modal));

    await screen.findByText(ru("adm.adlinks.created_title"));
    expect(screen.getByText(ru("adm.adlinks.url_pending"))).toBeTruthy();
  });

  it("бот старше готовых ссылок: не обещает «когда Telegram ответит», а говорит про обновление", async () => {
    // Кабинет обновили отдельно от бота 1.3.8: `url` не придёт никогда.
    appearance = { brand_name: "X" } as Appearance;
    create.mockResolvedValue({ id: 9, name: "Пост", code: "post", is_active: true, created_at: null });
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "Пост" } });
    fireEvent.click(submit(modal));

    await screen.findByText(ru("adm.adlinks.created_title"));
    expect(screen.queryByText(ru("adm.adlinks.url_pending"))).toBeNull();
    // Код подставляется в пояснение: человеку есть что собрать руками.
    expect(screen.getByText(ru("adm.adlinks.url_old_bot", { code: "post" }))).toBeTruthy();
  });

  it("отказ бэкенда виден в окне, окно не закрывается", async () => {
    create.mockRejectedValue(new ApiError(409, "Такой код уже существует"));
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "Пост" } });
    fireEvent.click(submit(modal));

    await waitFor(() => expect(within(modal).getByText("Такой код уже существует")).toBeTruthy());
    expect(screen.getByText(ru("adm.adlinks.create_title"))).toBeTruthy();
  });
});
