import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { ApiError } from "@/types/api";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

// Блок «Кто на резерве» существует ради одного: увидеть выданный резерв, которым
// НЕЛЬЗЯ пользоваться. Резерв даёт серверы через сквады, поэтому ACTIVE без сквадов —
// это «выдан», а в приложении пусто; именно так дефект и прятался, пока состояние
// панели не показывали. Тесты держат ровно это поведение.
let grants: () => Promise<unknown> = () => Promise.resolve({ items: [], active: 0, broken: 0 });
let squadCheck: () => Promise<unknown> = () =>
  Promise.resolve({ checked: true, ok: true, name: "Telegram-only", hosts: 2, problems: [] });

vi.mock("@/api/admin", () => ({
  reserveAdminApi: {
    // Карточка настроек не предмет этих тестов — отдаём минимум, чтобы она отрисовалась.
    get: () => Promise.resolve({ enabled: true, reserve_gb: 1, window_days: 7, squad_uuid: "sq-tg" }),
    update: (data: unknown) => Promise.resolve(data),
    grants: () => grants(),
    // Вердикт по сквад-резерву — карточка настроек спрашивает его при открытии.
    squadCheck: () => squadCheck(),
  },
  // Карточка настроек подтягивает сквады панели для выбора сквад-резерва.
  plansAdminApi: {
    squads: () => Promise.resolve({ internal: [{ uuid: "sq-tg", name: "Telegram-only" }], external: [], available: true }),
  },
}));

const { default: AdminReservePage } = await import("./AdminReservePage");

// Карточка настроек больше не хранит русский текст в коде: подписи приходят из
// словаря по ключам adm.settings.*. Тест сверяется с тем же словарём (и держит
// кабинет на русском), иначе он проверял бы не интерфейс, а копию строки.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const rx = (key: string, vars?: Record<string, string | number>) =>
  new RegExp(ru(key, vars).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminReservePage />
    </I18nProvider>,
  );

const card = () => screen.queryByText(ru("adm.reserve.grants_title"));

/**
 * Дождаться, пока приедут ВЫДАЧИ, а не пока появится заголовок блока.
 *
 * Заголовок «Кто на резерве» страница рисует всегда, с первого кадра, а строки —
 * только после ответа `grants()` (в разметке это `!error && data`). Поэтому
 * `waitFor(card)` возвращался мгновенно, ещё до загрузки, и следующая же строка
 * теста проверяла содержимое, которого пока нет. Локально успевало, на загруженном
 * раннере CI — нет: тест падал с «expected null not to be null» и выглядел как
 * поломка страницы, хотя ломался сам тест.
 *
 * Ждём то, что тест и проверяет: любой признак отрисованных данных.
 */
const waitForGrants = (anchor: () => unknown) => waitFor(() => expect(anchor()).not.toBeNull());

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
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  grants = () => Promise.resolve({ items: [], active: 0, broken: 0 });
  squadCheck = () =>
    Promise.resolve({ checked: true, ok: true, name: "Telegram-only", hosts: 2, problems: [] });
});
afterEach(cleanup);

describe("Кто на резерве", () => {
  it("рабочий резерв: показан сквад и расход, предупреждения нет", async () => {
    grants = () => Promise.resolve({ items: [grant()], active: 1, broken: 0 });
    renderPage();

    await waitForGrants(() => screen.queryByText("vasya"));
    expect(screen.queryByText("vasya")).not.toBeNull();
    expect(screen.queryByText("Reserve-1GB")).not.toBeNull();
    expect(
      screen.queryByText(ru("adm.reserve.traffic_value", { used: 0.2, limit: 1 })),
    ).not.toBeNull();
    expect(screen.queryByText(rx("adm.reserve.stat_broken"))).toBeNull();
  });

  it("выдан, но сквадов нет → строка помечена проблемой и счётчик её считает", async () => {
    grants = () =>
      Promise.resolve({
        items: [grant({ panel: { status: "ACTIVE", squads: [], traffic_limit_gb: 1, used_traffic_gb: 0 },
                        problem: "нет активных сквадов — в приложении будет пусто" })],
        active: 1,
        broken: 1,
      });
    renderPage();

    await waitForGrants(() => screen.queryByText("нет активных сквадов — в приложении будет пусто"));
    expect(screen.queryByText("нет активных сквадов — в приложении будет пусто")).not.toBeNull();
    expect(screen.queryByText(rx("adm.reserve.stat_broken"))).not.toBeNull();
  });

  it("израсходованный гигабайт — обычная пометка, а не предупреждение", async () => {
    grants = () =>
      Promise.resolve({
        items: [grant({ panel: { status: "LIMITED", squads: ["Reserve-1GB"], traffic_limit_gb: 1, used_traffic_gb: 1 },
                        note: "резерв израсходован" })],
        active: 1,
        broken: 0,
      });
    renderPage();

    await waitForGrants(() => screen.queryByText("резерв израсходован"));
    expect(screen.queryByText("резерв израсходован")).not.toBeNull();
    expect(screen.queryByText(rx("adm.reserve.stat_broken"))).toBeNull();
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
    renderPage();

    await waitForGrants(() => screen.queryByText("сломанный"));
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
    renderPage();

    await waitForGrants(() => screen.queryByText(ru("adm.reserve.ended")));
    expect(screen.getAllByText("vasya")).toHaveLength(2);
    expect(screen.queryByText(ru("adm.reserve.ended"))).not.toBeNull();
  });

  it("адаптер «Бедолаги»: ручки нет (501) → блока нет, страница цела", async () => {
    grants = () => Promise.reject(new ApiError(501, "Адаптер пока не умеет"));
    renderPage();

    // Ждём ИСЧЕЗНОВЕНИЯ блока, а не появления соседней карточки. Блок нарисован с
    // первого кадра и прячется только когда придёт 501; соседняя карточка грузится
    // сама по себе и успевает раньше — на медленном ответе тест проверял «блока
    // нет» в момент, когда ответа ещё не было, и видел живой заголовок.
    await waitFor(() => expect(card()).toBeNull());
    // getAllByText, а не queryByText: после того как блок «Кто на резерве» ушёл,
    // этот заголовок остаётся на странице не в одном месте, а queryByText на
    // нескольких совпадениях не возвращает null, а бросает.
    expect(screen.getAllByText(ru("adm.reserve.title")).length).toBeGreaterThan(0);
  });

  it("настоящая ошибка бэкенда не прячется, в отличие от 501", async () => {
    grants = () => Promise.reject(new ApiError(500, "Всё сломалось"));
    renderPage();

    await waitFor(() => expect(screen.queryByText("Всё сломалось")).not.toBeNull());
  });

  it("сломанный сквад-резерв виден сразу в настройках, а не из жалобы клиента", async () => {
    squadCheck = () =>
      Promise.resolve({
        checked: true,
        ok: false,
        name: "Пустой",
        hosts: 0,
        problems: ["у сквада нет ни одного инбаунда — подписка будет пустой"],
      });
    renderPage();

    await waitFor(() =>
      expect(
        screen.queryByText(
          ru("adm.settings.reserve_check_bad", {
            problems: "у сквада нет ни одного инбаунда — подписка будет пустой",
          }),
        ),
      ).not.toBeNull(),
    );
  });

  it("панель не ответила → вердикт не показываем (сквад не обвиняем зря)", async () => {
    squadCheck = () =>
      Promise.resolve({ checked: false, ok: false, name: null, hosts: 0, problems: ["панель недоступна"] });
    renderPage();

    await waitFor(() => expect(screen.getAllByText(ru("adm.reserve.title")).length).toBeGreaterThan(0));
    expect(screen.queryByText(/панель недоступна/)).toBeNull();
  });

  it("резерв ещё никому не выдавали → таблицы нет, но блок на месте", async () => {
    renderPage();

    await waitForGrants(() => screen.queryByText(ru("adm.reserve.empty")));
    expect(screen.queryByText(ru("adm.reserve.empty"))).not.toBeNull();
  });
});
