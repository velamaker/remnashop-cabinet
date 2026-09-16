import { useEffect, useMemo, useState } from "react";
import { useBranding } from "@/contexts/BrandingContext";
import { useT } from "@/i18n/I18nContext";

/**
 * Галочки согласия с документами при регистрации.
 *
 * ЗАЧЕМ. Бэкенд может ТРЕБОВАТЬ согласия с офертой и политикой: без списка
 * принятых документов он отвечает 428 и аккаунт не создаёт вовсе. Кабинет об этом
 * требовании не знал и галочек не слал — то есть на такой установке
 * зарегистрироваться было нельзя в принципе, а человек видел служебный отказ на
 * самой первой странице, до которой дошёл.
 *
 * ПОЧЕМУ ОТДЕЛЬНЫЙ КОМПОНЕНТ. Форма регистрации — файл, который у владельца этой
 * установки переоформлен под свою витрину и в общий репозиторий не уезжает. Суть
 * должна жить здесь, чтобы её получили все, а на странице осталась одна строка.
 *
 * ПОЧЕМУ НИЧЕГО НЕ РИСУЕТСЯ, КОГДА СОГЛАСИЕ НЕ ТРЕБУЕТСЯ. Лишняя обязательная
 * галочка — это лишний повод уйти с формы. Список документов приходит пустым, и
 * компонент возвращает null, не занимая места.
 */
/** Ключ документа → подпись из нашего словаря.
 *
 * Бэкенд отдаёт ТОЛЬКО ключи: «тексты и ссылки на них у кабинета свои» — это их
 * прямое решение. Публичной ручки с текстом оферты и политики у них нет (только
 * админские), поэтому дать ссылку не на что: читать документы человек будет уже
 * в кабинете, на странице «Информация». Незнакомый ключ печатаем как есть —
 * лучше сырое имя, чем молча пропущенная галочка, без которой не зарегистрируешься.
 *
 * Подписи ОТДЕЛЬНЫЕ, а не названия вкладок раздела «Информация»: там они стоят в
 * именительном падеже, и получалось «Я принимаю Конфиденциальность» — читается
 * как недоделанный перевод ровно там, где человек решает, доверять ли магазину.
 */
const TITLE_KEY: Record<string, string> = {
  public_offer: "legal.docOffer",
  privacy_policy: "legal.docPrivacy",
};

export function LegalConsent({
  onChange,
}: {
  /** Ключи принятых документов; пустой массив — пока согласия нет. */
  onChange: (acceptedKeys: string[], allAccepted: boolean) => void;
}) {
  const t = useT();
  const { legalDocuments, legalPrechecked } = useBranding();
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});

  // Оператор может попросить проставить галочки заранее — но само согласие всё
  // равно отправляется явным списком, а не подразумевается.
  useEffect(() => {
    if (!legalDocuments.length) return;
    setAccepted(Object.fromEntries(legalDocuments.map((key) => [key, legalPrechecked])));
  }, [legalDocuments, legalPrechecked]);

  const keys = useMemo(
    () => legalDocuments.filter((key) => accepted[key]),
    [legalDocuments, accepted],
  );

  useEffect(() => {
    onChange(keys, legalDocuments.length === 0 || keys.length === legalDocuments.length);
    // onChange меняется на каждый рендер родителя — следить за ним нельзя, иначе
    // получится бесконечный цикл обновлений.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys, legalDocuments.length]);

  if (!legalDocuments.length) return null;

  return (
    <div className="flex flex-col gap-2">
      {legalDocuments.map((key) => {
        const titleKey = TITLE_KEY[key];
        return (
          <label key={key} className="flex items-start gap-2 text-xs text-fg-muted">
            <input
              type="checkbox"
              checked={Boolean(accepted[key])}
              onChange={(e) => setAccepted((prev) => ({ ...prev, [key]: e.target.checked }))}
              className="mt-0.5 h-4 w-4 shrink-0 rounded border-[var(--border)] accent-[var(--accent)]"
            />
            <span className="min-w-0">
              {t("legal.accept")} {titleKey ? t(titleKey) : key}
            </span>
          </label>
        );
      })}
    </div>
  );
}
