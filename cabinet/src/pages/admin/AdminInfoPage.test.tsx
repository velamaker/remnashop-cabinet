import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Appearance } from "@/api/appearance";
import type { AdminInfoResponse } from "@/api/info";

/**
 * Редактор «Информации» с языками.
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. Со СТАРЫМ ботом параметр языка не существует: он сохранит
 * присланный текст как РУССКИЙ. Значит вкладки языков нельзя показывать, пока бот
 * не сказал, что умеет переводы, — одно «Сохранить» подменило бы русскую страницу
 * английской. И второе: в поле перевода нельзя подставлять русский текст «для
 * удобства» — иначе первое же сохранение превратит фолбэк в настоящий перевод и
 * русский текст перестанет обновляться вместе с оригиналом.
 */

const get = vi.fn();
const update = vi.fn();
vi.mock("@/api/info", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  infoAdminApi: {
    get: (lang?: string) => get(lang),
    update: (data: unknown, lang?: string) => update(data, lang),
  },
}));

let branding: { appearance: Appearance | null } = { appearance: null };
vi.mock("@/contexts/BrandingContext", () => ({ useBranding: () => branding }));

const AdminInfoPage = (await import("./AdminInfoPage")).default;

const RU = {
  faq: [{ q: "Что такое сервис?", a: "Это VPN." }],
  rules: "Русские правила",
  privacy: "Русская политика",
  offer: "Русская оферта",
  statuses: "Русские статусы",
};

function answer(over: Partial<AdminInfoResponse> = {}): AdminInfoResponse {
  return {
    ...RU,
    lang: "ru",
    base_lang: "ru",
    own: {},
    base: RU,
    translated_langs: [],
    ...over,
  } as AdminInfoResponse;
}

const oldBot = () => ({ brand_name: "X" }) as Appearance;
const newBot = () => ({ brand_name: "X", bot_capabilities: ["info_i18n"] }) as Appearance;

beforeEach(() => {
  get.mockReset();
  update.mockReset();
  get.mockResolvedValue(answer());
  update.mockImplementation(async (data: Partial<typeof RU>, lang?: string) =>
    answer({ lang: lang ?? "ru", own: lang && lang !== "ru" ? data : {} }),
  );
  branding = { appearance: newBot() };
});
afterEach(() => cleanup());

describe("редактор «Информации»: языки", () => {
  it("со старым ботом вкладок языков нет вовсе", async () => {
    branding = { appearance: oldBot() };
    render(<AdminInfoPage />);
    await waitFor(() => expect(screen.getByText("FAQ")).toBeTruthy());
    expect(screen.queryByText("Язык текстов")).toBeNull();
    expect(screen.queryByText("English")).toBeNull();
  });

  it("с новым ботом язык выбирается, и контент запрашивается на нём", async () => {
    render(<AdminInfoPage />);
    await waitFor(() => expect(screen.getByText("Язык текстов")).toBeTruthy());
    expect(get).toHaveBeenCalledWith("ru");

    get.mockResolvedValue(answer({ lang: "en", own: {}, translated_langs: [] }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(get).toHaveBeenCalledWith("en"));
  });

  it("непереведённый раздел показывается пустым, а не русским текстом", async () => {
    render(<AdminInfoPage />);
    await waitFor(() => expect(screen.getByText("Язык текстов")).toBeTruthy());

    get.mockResolvedValue(answer({ lang: "en", own: {} }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(screen.getByText("Нет перевода — покажем русский")).toBeTruthy());

    fireEvent.click(screen.getByText("Правила"));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(area.value).toBe(""); // пусто = «не переводили»
    expect(area.placeholder).toBe("Русские правила"); // русский — подсказкой
  });

  it("сохранение перевода уходит с кодом языка", async () => {
    render(<AdminInfoPage />);
    await waitFor(() => expect(screen.getByText("Язык текстов")).toBeTruthy());

    get.mockResolvedValue(answer({ lang: "en", own: {} }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(get).toHaveBeenCalledWith("en"));

    fireEvent.click(screen.getByText("Правила"));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    fireEvent.change(area, { target: { value: "English rules" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(update).toHaveBeenCalled());
    const [data, lang] = update.mock.calls[0]!;
    expect(lang).toBe("en");
    expect((data as typeof RU).rules).toBe("English rules");
  });

  it("«Вставить русский текст» переносит оригинал в поле перевода", async () => {
    render(<AdminInfoPage />);
    await waitFor(() => expect(screen.getByText("Язык текстов")).toBeTruthy());
    get.mockResolvedValue(answer({ lang: "en", own: {} }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(screen.getByText("Вставить русский текст")).toBeTruthy());

    fireEvent.click(screen.getByText("Оферта"));
    fireEvent.click(screen.getByText("Вставить русский текст"));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(area.value).toBe("Русская оферта");
  });

  it("русская вкладка правится как раньше: сохраняем без языка", async () => {
    render(<AdminInfoPage />);
    await waitFor(() => expect(screen.getByText("Язык текстов")).toBeTruthy());
    fireEvent.click(screen.getByText("Правила"));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(area.value).toBe("Русские правила");
    fireEvent.change(area, { target: { value: "Новые правила" } });
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0]![1]).toBe("ru");
  });
});
