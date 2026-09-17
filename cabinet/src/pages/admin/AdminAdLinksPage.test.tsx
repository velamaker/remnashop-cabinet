import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/types/api";
import type { Appearance } from "@/api/appearance";

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

async function openModal() {
  render(<AdminAdLinksPage />);
  await screen.findByText("Ссылок нет");
  fireEvent.click(screen.getByRole("button", { name: /Создать/ }));
  return screen.getByText("Новая рекламная ссылка").closest("div.w-full") as HTMLElement;
}

const nameInput = () => screen.getByLabelText(/Название/) as HTMLInputElement;
const codeInput = () => screen.getByLabelText(/Код ссылки/) as HTMLInputElement;
const submit = (modal: HTMLElement) => within(modal).getByRole("button", { name: "Создать" });

describe("окно создания рекламной ссылки", () => {
  beforeEach(() => {
    create.mockReset();
    appearance = { brand_name: "X", bot_capabilities: ["ad_link_url"] } as Appearance;
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
  });
  afterEach(() => cleanup());

  it("поля пустые, а подсказки явно помечены как примеры", async () => {
    await openModal();
    expect(nameInput().value).toBe("");
    expect(codeInput().value).toBe("");
    expect(nameInput().placeholder).toMatch(/^Например/);
    expect(codeInput().placeholder).toMatch(/^Например/);
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

    expect(within(modal).getByText(/Только латиница/)).toBeTruthy();
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

    await screen.findByText("Ссылка создана");
    expect(create).toHaveBeenCalledWith({ name: "Сторис у блогера", code: "storis_u_blogera" });
    expect(screen.getByText("https://t.me/example_bot?start=ad_storis_u_blogera")).toBeTruthy();

    fireEvent.click(screen.getByTitle("Копировать ссылку"));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("https://t.me/example_bot?start=ad_storis_u_blogera");
  });

  it("без адреса бота честно показывает код с пояснением", async () => {
    create.mockResolvedValue({ id: 8, name: "Пост", code: "post", is_active: true, created_at: null });
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "Пост" } });
    fireEvent.click(submit(modal));

    await screen.findByText("Ссылка создана");
    expect(screen.getByText(/Адрес бота сейчас не получить/)).toBeTruthy();
  });

  it("бот старше готовых ссылок: не обещает «когда Telegram ответит», а говорит про обновление", async () => {
    // Кабинет обновили отдельно от бота 1.3.8: `url` не придёт никогда.
    appearance = { brand_name: "X" } as Appearance;
    create.mockResolvedValue({ id: 9, name: "Пост", code: "post", is_active: true, created_at: null });
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "Пост" } });
    fireEvent.click(submit(modal));

    await screen.findByText("Ссылка создана");
    expect(screen.queryByText(/когда Telegram ответит/)).toBeNull();
    expect(screen.getByText(/появится после обновления бота/).textContent).toContain("start=ad_post");
  });

  it("отказ бэкенда виден в окне, окно не закрывается", async () => {
    create.mockRejectedValue(new ApiError(409, "Такой код уже существует"));
    const modal = await openModal();
    fireEvent.change(nameInput(), { target: { value: "Пост" } });
    fireEvent.click(submit(modal));

    await waitFor(() => expect(within(modal).getByText("Такой код уже существует")).toBeTruthy());
    expect(screen.getByText("Новая рекламная ссылка")).toBeTruthy();
  });
});
