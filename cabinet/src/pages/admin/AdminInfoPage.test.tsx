import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Appearance } from "@/api/appearance";
import type { AdminInfoResponse } from "@/api/info";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

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

// `can` в подделке обязателен: вкладки языков закрыты ДВУМЯ механизмами — токеном
// нашего бота и признаком возможности, который выключает адаптер чужого бэкенда.
let branding: { appearance: Appearance | null; can: (k: string) => boolean } = {
  appearance: null,
  can: () => true,
};
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

// Экран больше не хранит русский текст в коде: подписи приходят из словаря по
// ключам adm.info.*. Тест сверяется с тем же словарём (и держит кабинет на
// русском), иначе он проверял бы не интерфейс, а копию строки.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminInfoPage />
    </I18nProvider>,
  );

const oldBot = () => ({ brand_name: "X" }) as Appearance;
const newBot = () => ({ brand_name: "X", bot_capabilities: ["info_i18n"] }) as Appearance;

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  get.mockReset();
  update.mockReset();
  get.mockResolvedValue(answer());
  update.mockImplementation(async (data: Partial<typeof RU>, lang?: string) =>
    answer({ lang: lang ?? "ru", own: lang && lang !== "ru" ? data : {} }),
  );
  branding = { appearance: newBot(), can: () => true };
});
afterEach(() => cleanup());

describe("редактор «Информации»: языки", () => {
  it("чужой бэкенд выключил возможность — вкладок нет, даже если бот «умеет»", async () => {
    // Поверх «Бедолаги» тексты живут в ИХ CMS со своим языком по умолчанию: наш
    // параметр языка она не знает, и сохранение перевода затёрло бы основной текст.
    branding = { appearance: newBot(), can: () => false };
    renderPage();
    await waitFor(() => expect(screen.getByText(ru("adm.info.tab_faq"))).toBeTruthy());
    expect(screen.queryByText(ru("adm.info.lang_label"))).toBeNull();
  });

  it("со старым ботом вкладок языков нет вовсе", async () => {
    branding = { appearance: oldBot(), can: () => true };
    renderPage();
    await waitFor(() => expect(screen.getByText(ru("adm.info.tab_faq"))).toBeTruthy());
    expect(screen.queryByText(ru("adm.info.lang_label"))).toBeNull();
    expect(screen.queryByText("English")).toBeNull();
  });

  it("с новым ботом язык выбирается, и контент запрашивается на нём", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(ru("adm.info.lang_label"))).toBeTruthy());
    expect(get).toHaveBeenCalledWith("ru");

    get.mockResolvedValue(answer({ lang: "en", own: {}, translated_langs: [] }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(get).toHaveBeenCalledWith("en"));
  });

  it("непереведённый раздел показывается пустым, а не русским текстом", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(ru("adm.info.lang_label"))).toBeTruthy());

    get.mockResolvedValue(answer({ lang: "en", own: {} }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(screen.getByText(ru("adm.info.no_translation"))).toBeTruthy());

    fireEvent.click(screen.getByText(ru("adm.info.tab_rules")));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(area.value).toBe(""); // пусто = «не переводили»
    expect(area.placeholder).toBe("Русские правила"); // русский — подсказкой
  });

  it("сохранение перевода уходит с кодом языка", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(ru("adm.info.lang_label"))).toBeTruthy());

    get.mockResolvedValue(answer({ lang: "en", own: {} }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(get).toHaveBeenCalledWith("en"));

    fireEvent.click(screen.getByText(ru("adm.info.tab_rules")));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    fireEvent.change(area, { target: { value: "English rules" } });
    fireEvent.click(screen.getByText(ru("adm.info.save")));

    await waitFor(() => expect(update).toHaveBeenCalled());
    const [data, lang] = update.mock.calls[0]!;
    expect(lang).toBe("en");
    expect((data as typeof RU).rules).toBe("English rules");
  });

  it("«Вставить русский текст» переносит оригинал в поле перевода", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(ru("adm.info.lang_label"))).toBeTruthy());
    get.mockResolvedValue(answer({ lang: "en", own: {} }));
    fireEvent.click(screen.getByText("English"));
    await waitFor(() => expect(screen.getByText(ru("adm.info.paste_base"))).toBeTruthy());

    fireEvent.click(screen.getByText(ru("adm.info.tab_offer")));
    fireEvent.click(screen.getByText(ru("adm.info.paste_base")));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(area.value).toBe("Русская оферта");
  });

  it("русская вкладка правится как раньше: сохраняем без языка", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(ru("adm.info.lang_label"))).toBeTruthy());
    fireEvent.click(screen.getByText(ru("adm.info.tab_rules")));
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(area.value).toBe("Русские правила");
    fireEvent.change(area, { target: { value: "Новые правила" } });
    fireEvent.click(screen.getByText(ru("adm.info.save")));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0]![1]).toBe("ru");
  });
});
