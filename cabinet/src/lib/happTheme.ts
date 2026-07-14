// Тема оформления Happ (заголовок подписки color-profile, iOS).
// Формат — JSON с цветами #RRGGBBAA; поля и их смысл взяты из dev-docs Happ.

const hex = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");

function parse(color: string): [number, number, number] {
  const c = color.trim().replace("#", "");
  const full = c.length === 3 ? c.split("").map((ch) => ch + ch).join("") : c.slice(0, 6);
  return [
    parseInt(full.slice(0, 2), 16) || 0,
    parseInt(full.slice(2, 4), 16) || 0,
    parseInt(full.slice(4, 6), 16) || 0,
  ];
}

/** Цвет + альфа в формате Happ: #RRGGBBAA. */
function rgba(color: string, alpha = "FF"): string {
  const [r, g, b] = parse(color);
  return `#${hex(r)}${hex(g)}${hex(b)}${alpha}`.toUpperCase();
}

/** Смешивание двух цветов: 0 = первый, 1 = второй. */
function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = parse(a);
  const [r2, g2, b2] = parse(b);
  return `#${hex(r1 + (r2 - r1) * t)}${hex(g1 + (g2 - g1) * t)}${hex(b1 + (b2 - b1) * t)}`;
}

/** Тема Happ из цветов кабинета: тёмный фон с градиентом в акцент, кнопка — акцент. */
export function buildHappTheme(accent: string, accent2: string, bg: string): string {
  const theme = {
    backgroundGradientRotationAngle: 37.1,
    backgroundColors: [rgba(bg), rgba(mix(bg, accent, 0.35)), rgba(accent, "7F")],
    backgroundGradientColorIntensity: 1,
    backgroundImageType: "system",
    elipseColors: [rgba(accent2), rgba(accent, "E0"), rgba(mix(accent, accent2, 0.5), "B0")],
    buttonColor: rgba(accent),
    buttonTextColor: "#FFFFFFFF",
    buttonTimerColor: "#FFFFFFFF",
    buttonImageType: "light",
    powerIconColor: rgba(mix(bg, "#000000", 0.2)),
    topBarButtonsColor: "#FFFFFFFF",
    additionalOptionsButtonColor: "#FFFFFFFF",
    supportIconColor: "#FFFFFFFF",
    profileWebPageIconColor: rgba(mix(accent2, "#ffffff", 0.4)),
    subsHeaderColor: rgba(mix(bg, accent, 0.25)),
    subHeaderButtonColor: "#FFFFFFFF",
    disclosureHeaderTextColor: "#FFFFFFFF",
    disclosureSubHeaderTextColor: rgba(mix(accent2, "#ffffff", 0.55)),
    subscriptionInfoBackgroundColor: rgba(mix(bg, "#000000", 0.15)),
    subscriptionInfoTextColor: "#FFFFFFFF",
    subscriptionTrafficBackgroundColor: rgba(mix(accent, bg, 0.35)),
    serverRowBackgroundColor: rgba(mix(bg, "#000000", 0.25), "67"),
    selectedServerRowColor: rgba(mix(accent, bg, 0.5), "B5"),
    serverRowTitleTextColor: "#FFFFFFFF",
    serverRowSubTitleTextColor: rgba(mix(accent2, "#ffffff", 0.55)),
    serverRowChevronColor: "#FFFFFFFF",
  };
  return JSON.stringify(theme, null, 1);
}

/** Текущие цвета кабинета (учитывают бренд — переменные подменяет BrandingContext). */
export function cabinetColors(): { accent: string; accent2: string; bg: string } {
  const css = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) => {
    const v = css.getPropertyValue(name).trim();
    return v.startsWith("#") ? v : fallback;
  };
  return {
    accent: read("--accent", "#4d8bff"),
    accent2: read("--accent-2", "#2bd4ee"),
    bg: read("--bg", "#0a0d15"),
  };
}
