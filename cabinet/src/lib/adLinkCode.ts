/**
 * Код рекламной ссылки: подсказка из названия и проверка.
 *
 * Код уходит в Telegram-ссылку `?start=…`, а Telegram пропускает там только
 * латиницу, цифры, `_` и `-` (до 64 символов вместе с префиксом бота). Код с
 * пробелом или кириллицей раньше сохранялся, ссылка выглядела рабочей, а переходы
 * по ней не считались. Бэкенды проверяют то же правило; здесь — чтобы человек
 * увидел причину до нажатия «Создать», а не после.
 */

export const AD_CODE_MAX = 61;

const ALLOWED = /^[A-Za-z0-9_-]+$/;

const TRANSLIT: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i",
  й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t",
  у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "",
  э: "e", ю: "yu", я: "ya",
};

/** Текст ошибки для кода; null — код годится. Пустой код ошибкой не считаем:
 *  о нём скажет обязательность поля, а не красная строка до первого символа. */
export function adCodeProblem(code: string): string | null {
  const value = code.trim();
  if (!value) return null;
  if (value.length > AD_CODE_MAX) return `Код длиннее ${AD_CODE_MAX} символов`;
  if (!ALLOWED.test(value)) return "Только латиница, цифры, «_» и «-» — без пробелов и русских букв";
  return null;
}

/** Код из названия: «Сторис у блогера» → «storis_u_blogera». Пусто, если из
 *  названия ничего не выходит (например, одни эмодзи) — тогда код вводят сами. */
export function suggestAdCode(name: string): string {
  const latin = Array.from(name.toLowerCase())
    .map((ch) => (ch in TRANSLIT ? TRANSLIT[ch] : ch))
    .join("");
  return latin
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40)
    .replace(/_+$/g, "");
}
