/**
 * Возможности НАШЕГО бота, без которых часть кабинета не работает.
 *
 * ЗАЧЕМ. Кабинет бывает новее бота: оператор обновил только кабинет
 * (`./update.sh --cabinet-only`) или кабинет стоит на отдельном сервере. Раньше
 * такой кабинет показывал пункты меню, опции и тумблеры, которые у старого бота
 * упирались в 404 или «сохранялись» без сохранения. Теперь бот сам перечисляет, что
 * умеет (`bot_capabilities` в GET /api/appearance), а кабинет прячет остальное.
 * Решаем не по номеру версии: VERSION поднимается только при выпуске и на части
 * установок примонтирован с хоста, а список живёт в коде бота и не врёт.
 *
 * ТРИ ПРАВИЛА ЧТЕНИЯ (botHas):
 *  1. Есть `features` — это адаптер чужого бота, он решает сам, список нашего бота не
 *     смотрим вовсе (поверх «Бедолаги» поведение не меняется ни на байт).
 *  2. Нет поля `bot_capabilities` — бот 1.3.8 или старше: ни одного токена.
 *  3. Всё, чего нет в FEATURE_NEEDS_BOT / PAGE_NEEDS_BOT, от бота не зависит и
 *     показывается как раньше («нет ключа = умеет», см. lib/features.ts).
 *
 * КОГДА НУЖЕН ТОКЕН. Обязателен, если со старым ботом кабинет:
 *  1) показывает вход в функцию ДО ответа бота — пункт меню, плитку, опцию, кнопку,
 *     тумблер;
 *  2) пишет поле, которое старый бот молча выбросит (pydantic лишнее игнорирует →
 *     «Сохранено» без сохранения; так было с тумблером апселла);
 *  3) обещает деньги, дни или необратимое, зависящее от нового поведения бота.
 * Не нужен для скрытия, если блок рисуется только по данным ответа, а 404 становится
 * «блока нет» без ошибки. Тогда путь — в PATH_GRACEFUL (botCapabilities.paths.ts) с
 * причиной, а токен всё равно заводим «справочным»: по нему update.sh и экран
 * «Обновления» называют, чего не будет до обновления бота.
 * Сторож путей ловит новые РУЧКИ сам; новое ПОЛЕ в старой ручке — только это правило.
 *
 * КАК ДОБАВИТЬ ФУНКЦИЮ (пример — докупка +1 устройства, следующая в очереди):
 *  1. admin_src/src/web/cabinet_capabilities.py: строка `    "extra_device",` в
 *     CABINET_CAPABILITIES — в том же коммите, что и ручки бота;
 *  2. здесь, в BOT_CAPABILITIES:
 *       extra_device: { since: "<номер выпуска>", label: "Докупка +1 устройства к подписке" },
 *  3. вход в функцию: ключ "extra_device" в FeatureKey (lib/features.ts) и
 *     `extra_device: "extra_device"` в FEATURE_NEEDS_BOT; админская страница —
 *     `"/admin/extra-device": "extra_device"` в PAGE_NEEDS_BOT;
 *  4. adapter/compose.py: FEATURE_OVERRIDES["extra_device"] = False — у «Бедолаги»
 *     своя докупка, а без ключа кабинет посчитал бы функцию доступной;
 *  5. botCapabilities.paths.ts: новые пути — в PATH_NEEDS_BOT (сторож потребует сам).
 * Изменения ТОЛЬКО в боте (кабинет не трогают) сюда не пишутся: они живут в
 * BOT_ONLY_CHANGES в cabinet_capabilities.py — их называет итог update.sh.
 *
 * ФОРМАТ СТРОК BOT_CAPABILITIES НЕ МЕНЯТЬ: update.sh читает их sed'ом — одна запись
 * на строке, `  токен: { since: "X.Y.Z", label: "…" },`, без двойных кавычек в
 * подписи (заперто botCapabilities.test.ts).
 */
import type { Appearance } from "@/api/appearance";
import type { FeatureKey } from "./features";

export const BOT_CAPABILITIES = {
  bulk_jobs: { since: "1.3.9", label: "Пользователи → массово по фильтру: добавить дни и написать" },
  renewal_discount: { since: "1.3.9", label: "Скидка на продление до окончания подписки" },
  device_upsell: { since: "1.3.9", label: "«Нужно больше устройств?» и предупреждение о смене тарифа" },
  broadcast_expiring: { since: "1.3.9", label: "Рассылка «Истекают скоро»" },
  digest_email: { since: "1.3.9", label: "Месячная сводка письмом" },
  refunds_tile: { since: "1.3.9", label: "Плитка «Возвраты (30 дн)» в статистике" },
  ad_link_url: { since: "1.3.9", label: "Готовая ссылка в окне новой рекламной ссылки" },
  plan_change_carry: { since: "1.3.9", label: "Перенос оставшихся дней при смене тарифа" },
} as const;

export type BotCap = keyof typeof BOT_CAPABILITIES;

/**
 * Возможности кабинета (canFeature), которые прячутся, пока бот не прислал токен.
 * Остальные токены справочные — их функции и так рисуются по данным ответа:
 *  - broadcast_expiring: сегмента нет, пока его ключа нет в счётчиках рассылок;
 *  - digest_email: карточка сводки прячется на 404;
 *  - refunds_tile: плитки нет без поля `refunds` в статистике;
 *  - ad_link_url: без `url` окно показывает код (и говорит, что ссылка появится
 *    после обновления бота);
 *  - plan_change_carry: перенос обещается только по `plan_change_carry_active` из
 *    витрины; старый бот его не шлёт, и оплата предупреждает о сгорании по-старому.
 */
export const FEATURE_NEEDS_BOT: Partial<Record<FeatureKey, BotCap>> = {
  // Опции «Добавить дни…» и «Написать сообщение…» видны до первого запроса, а
  // предпросмотр у старого бота падал с «Не удалось посчитать».
  bulk_days: "bulk_jobs",
  bulk_message: "bulk_jobs",
  // Тумблер в «Доступ и язык» писал поле, которое старый бот выбрасывал; карточка
  // заодно не ходит за витриной впустую.
  device_upsell: "device_upsell",
};

/** Страницы админки, которых нет в меню, пока бот не прислал токен. */
export const PAGE_NEEDS_BOT: Readonly<Record<string, BotCap>> = {
  "/admin/renewal-discount": "renewal_discount",
};

/** Что показать на прямом заходе по адресу спрятанной страницы. */
export const BOT_PAGE_NOTE =
  "Этот раздел появится после обновления бота: кабинет уже новее бота, к которому он подключён. " +
  "На сервере бота выполните ./update.sh --with-bot — кабинет пересобирать не нужно, раздел " +
  "появится при следующем открытии админки.";

/** Умеет ли работающий бот эту возможность (правила — в шапке файла). */
export function botHas(appearance: Appearance | null | undefined, cap: BotCap): boolean {
  // Оформление ещё не пришло — не знаем, что за бот: новое не показываем.
  if (!appearance) return false;
  // Чужой бэкенд решает сам через features (и pages): список нашего бота не нужен.
  if (appearance.features != null && typeof appearance.features === "object") return true;
  const caps = appearance.bot_capabilities;
  return Array.isArray(caps) && caps.includes(cap);
}

/** Чего не хватает боту из манифеста. Для чужого бэкенда и неизвестного — пусто. */
export function missingBotCaps(appearance: Appearance | null | undefined): BotCap[] {
  if (!appearance) return [];
  return (Object.keys(BOT_CAPABILITIES) as BotCap[]).filter((cap) => !botHas(appearance, cap));
}

/** Токен, от которого зависит страница (или её вложенный адрес), либо null. */
export function pageBotCap(path: string): BotCap | null {
  for (const [page, cap] of Object.entries(PAGE_NEEDS_BOT)) {
    if (path === page || path.startsWith(page + "/")) return cap;
  }
  return null;
}

/** Готов ли бот для страницы админки. Страницы вне манифеста — всегда да. */
export function pageBotReady(appearance: Appearance | null | undefined, path: string): boolean {
  const cap = pageBotCap(path);
  return cap === null || botHas(appearance, cap);
}
