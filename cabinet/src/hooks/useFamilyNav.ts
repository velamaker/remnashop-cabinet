import { useEffect, useState } from "react";
import { familyApi, familyVisible } from "@/api/family";
import { useAuth } from "@/contexts/AuthContext";
import { useBranding } from "@/contexts/BrandingContext";

/**
 * Показывать ли пункт «Семья» в меню.
 *
 * Возможность бота (`family_profiles`) говорит только, что бот УМЕЕТ семью. Пункт же
 * нужен не всем: функция выключена владельцем сервиса или тариф человека обычный —
 * «Семья», упирающаяся в «ваш тариф не семейный», была бы рекламой, а не помощью.
 * Поэтому спрашиваем сам бот — лёгким запросом без походов в панель — и один раз за
 * сессию на человека: меню перерисовывается на каждой странице.
 *
 * Любая ошибка — пункта нет: раздел необязательный, и падать меню из-за него нельзя.
 */

let cached: Promise<boolean> | null = null;
let cachedFor: string | null = null;

/** Забыть ответ: страница «Семья» зовёт после создания и удаления профиля. */
export function resetFamilyNav(): void {
  cached = null;
  cachedFor = null;
}

export function useFamilyNav(): boolean {
  const { appearance, can } = useBranding();
  const { user } = useAuth();
  // Без оформления не знаем, что за бэкенд: `can` на пустом оформлении отвечает «да».
  const allowed = appearance != null && can("family_profiles") && user != null;
  const who = user ? `${user.telegram_id ?? ""}|${user.email ?? ""}|${user.name}` : null;
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!allowed || who == null) {
      setVisible(false);
      return;
    }
    if (cached == null || cachedFor !== who) {
      cachedFor = who;
      cached = familyApi
        .summary()
        .then(familyVisible)
        .catch(() => false);
    }
    let alive = true;
    void cached.then((value) => {
      if (alive) setVisible(value);
    });
    return () => {
      alive = false;
    };
  }, [allowed, who]);

  return visible;
}
