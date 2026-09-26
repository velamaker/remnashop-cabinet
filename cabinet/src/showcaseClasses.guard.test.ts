import { describe, expect, it } from "vitest";
import ts from "typescript";

/**
 * Сторож: примитивы оформления витрины не утекают в опубликованные страницы.
 *
 * ЧТО СЛУЧИЛОСЬ. Классы `panel`, `panel-sheen`, `bg-grain`, `hairline-grid`,
 * `aurora`, `mono-label`, `btn-hero`, `font-display` живут в index.css владельца
 * (редизайн витрины) и в опубликованный index.css не попадают — так задумано.
 * Но ими успели оформить страницы, которые публикуются: подтверждение почты по
 * ссылке, отписку от сводки, согласие при входе из Telegram, рефералку. У всех
 * чужих установок эти страницы выходили голыми: ни рамки, ни кнопки, ни фона.
 * Tailwind на незнакомый класс не ругается, сборка зелёная — заметить можно было
 * только глазами, и только не у себя.
 *
 * ПРАВИЛО. Класс из списка можно ставить, только если он определён в index.css
 * ЭТОЙ копии. В опубликованной его нет — сторож падает и называет файл и строку;
 * у владельца, где примитивы на месте, сторож молчит. Замены — публикуемые
 * примитивы, как в GiftCertificatePage.tsx: `card-hero`/`surface`, `btn-gradient
 * text-white`, `font-mono text-[11px] … uppercase tracking-[0.2em]`, свечение —
 * утилитами (`rounded-full opacity-50 blur-3xl` + radial-gradient).
 *
 * КАК ИЩЕМ. Разбираем .tsx компилятором TypeScript и смотрим на строки, которые
 * являются списком классов: всё внутри атрибута className (с шаблонами и
 * тернарниками), аргументы clsx/cn и строки-«списки утилит» в переменных
 * (`const button = "btn-hero mt-6 …"`). Комментарии и обычные строки вроде
 * `runSync("panel")` не считаются — иначе сторож кричал бы на слово «panel»
 * в админке.
 */

export const SHOWCASE_ONLY = [
  "panel",
  "panel-sheen",
  "bg-grain",
  "hairline-grid",
  "aurora",
  "mono-label",
  "btn-hero",
  "font-display",
] as const;

const SOURCES = import.meta.glob<string>(["/src/**/*.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});
// index.css читаем с диска, а не через `?raw`: в тестах обработка CSS выключена
// (vitest.config.ts, `css: false`), и импорт любого .css приходит пустой строкой —
// сторож считал бы, что не определено ничего. ts.sys — чтение файлов из пакета
// typescript, он уже в devDependencies; типов node в кабинете нет. Путь — строкой
// из import.meta.url: `new URL("./index.css", import.meta.url)` Vite переписывает
// в адрес ассета («/src/index.css»), и чтение молча промахивается.
const CSS_PATH = decodeURIComponent(import.meta.url.replace(/^file:\/\//, "").replace(/[^/]+$/, "index.css"));
const CSS = ts.sys.readFile(CSS_PATH) ?? "";

const CLASS_CALLS = new Set(["clsx", "cn", "classNames", "twMerge"]);
// Токен, похожий на утилиту: строчные, цифры и знаки вариантов/произвольных значений.
const CLASSY = /^!?-?[a-z0-9][a-z0-9_:/.%#[\]()'",=&>*+@!-]*$/;

/** Классы из строки: без `!` важности и без префиксов вариантов (`hover:`, `sm:`). */
function tokens(text: string): string[] {
  return text
    .split(/\s+/)
    .filter(Boolean)
    .map((tok) => tok.replace(/^!/, "").split(":").pop() ?? "");
}

/** Строка сама по себе выглядит как список классов («btn-hero mt-6 h-11 …»). */
function looksLikeClassList(text: string): boolean {
  const raw = text.split(/\s+/).filter(Boolean);
  return raw.length >= 2 && raw.every((tok) => CLASSY.test(tok)) && raw.some((tok) => tok.includes("-"));
}

const COMPARISON = new Set([
  ts.SyntaxKind.EqualsEqualsEqualsToken,
  ts.SyntaxKind.ExclamationEqualsEqualsToken,
  ts.SyntaxKind.EqualsEqualsToken,
  ts.SyntaxKind.ExclamationEqualsToken,
  ts.SyntaxKind.InKeyword,
]);

/**
 * Строка — часть значения className (или аргумент clsx), а не условие рядом с ним.
 * `className={busy === "panel" ? "animate-spin" : ""}`: «panel» здесь — сравниваемое
 * значение, а классы — только ветки тернарника.
 */
function insideClassContext(node: ts.Node): boolean {
  let child: ts.Node = node;
  for (let cur = node.parent; cur; child = cur, cur = cur.parent) {
    if (ts.isBinaryExpression(cur) && COMPARISON.has(cur.operatorToken.kind)) return false;
    if (ts.isConditionalExpression(cur) && child === cur.condition) return false;
    if (ts.isElementAccessExpression(cur) && child === cur.argumentExpression) return false;
    if (ts.isCallExpression(cur) && !(ts.isIdentifier(cur.expression) && CLASS_CALLS.has(cur.expression.text))) {
      return false;
    }
    if (ts.isJsxAttribute(cur)) {
      const name = cur.name.getText();
      return name === "className" || name === "class";
    }
    if (ts.isCallExpression(cur) && ts.isIdentifier(cur.expression) && CLASS_CALLS.has(cur.expression.text)) {
      return true;
    }
  }
  return false;
}

export interface Usage {
  file: string;
  line: number;
  cls: string;
}

/** Где в исходнике как КЛАСС стоит что-то из `watched`. */
export function findClassUsages(file: string, source: string, watched: readonly string[]): Usage[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const want = new Set(watched);
  const out: Usage[] = [];
  const visit = (node: ts.Node) => {
    let text: string | null = null;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) text = node.text;
    else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) text = node.text;
    if (text !== null && (insideClassContext(node) || looksLikeClassList(text))) {
      for (const cls of tokens(text)) {
        if (want.has(cls)) {
          const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf));
          out.push({ file, line: line + 1, cls });
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

/** Определён ли класс селектором в CSS (в том числе `.x::before`, `.theme-light .x`). */
export function definedInCss(css: string, cls: string): boolean {
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const escaped = cls.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\.${escaped}(?![\\w-])`).test(plain);
}

describe("сторож классов витрины", () => {
  it("поиск не прошёл впустую", () => {
    // Сломается поиск файлов или чтение CSS — сторож не должен молча позеленеть.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    expect(definedInCss(CSS, "card-hero")).toBe(true);
    expect(definedInCss(CSS, "btn-gradient")).toBe(true);
  });

  it("находит класс в className, шаблоне, clsx и переменной — и не путает со словом", () => {
    const sample = [
      `const a = <div className="panel p-6" />;`,
      "const b = <div className={`x ${on ? \"hover:btn-hero\" : \"\"} y`} />;",
      `const c = clsx("aurora", big && "p-4");`,
      `const d = "mono-label mt-2 text-fg";`,
      `const e = <h1 className={\`font-display \${size}\`} />;`,
      // Не классы: строковый аргумент, поле объекта, сравнение внутри className.
      `runSync("panel"); const f = g.panel?.status; const h = "Admin panel";`,
      "const i = <b className={`h-4 ${busy === \"panel\" || m[\"aurora\"] ? \"animate-spin\" : \"\"}`} />;",
      `// className="bg-grain" в комментарии не считается`,
    ].join("\n");
    const found = findClassUsages("sample.tsx", sample, SHOWCASE_ONLY).map((u) => `${u.line}:${u.cls}`);
    expect(found).toEqual(["1:panel", "2:btn-hero", "3:aurora", "4:mono-label", "5:font-display"]);
  });

  it("определение в CSS узнаётся и по псевдоэлементу, и не путается с длинным именем", () => {
    expect(definedInCss(".panel-sheen::before { content: '' }", "panel-sheen")).toBe(true);
    expect(definedInCss(".theme-light .aurora { opacity: .3 }", "aurora")).toBe(true);
    expect(definedInCss(".panel-sheen { }", "panel")).toBe(false);
    expect(definedInCss("/* .panel { } */", "panel")).toBe(false);
  });

  it("классы витрины стоят только там, где index.css их определяет", () => {
    const missing = SHOWCASE_ONLY.filter((cls) => !definedInCss(CSS, cls));
    const bad = Object.entries(SOURCES)
      .flatMap(([file, text]) => findClassUsages(file, text, missing))
      .map((u) => `${u.file}:${u.line} — «${u.cls}»`);
    expect(
      bad,
      `классов нет в index.css, страница выйдет без оформления у всех установок:\n${bad.join("\n")}`,
    ).toEqual([]);
  });
});
