// Брендовые иконки платформ для списка устройств (как в боте/панели).
// SVG-логотипы — чёткие на всех ОС, в отличие от emoji (у Apple нет emoji-логотипа).

type Platform = "apple" | "windows" | "android" | "linux" | "unknown";

function detectPlatform(...parts: (string | null | undefined)[]): Platform {
  const s = parts.filter(Boolean).join(" ").toLowerCase();
  if (/iphone|ipad|ipod|ios|ipados|mac|macos|darwin|apple/.test(s)) return "apple";
  if (/windows|win32|win64|microsoft/.test(s)) return "windows";
  if (/android/.test(s)) return "android";
  if (/linux|ubuntu|debian|fedora|arch/.test(s)) return "linux";
  return "unknown";
}

export function PlatformIcon({
  platform,
  model,
  os,
  userAgent,
  className = "h-5 w-5",
}: {
  platform?: string | null;
  model?: string | null;
  os?: string | null;
  userAgent?: string | null;
  className?: string;
}) {
  const p = detectPlatform(platform, model, os, userAgent);

  switch (p) {
    case "apple":
      return (
        <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-label="Apple">
          <path d="M16.365 1.43c0 1.14-.493 2.27-1.177 3.08-.744.9-1.99 1.57-2.987 1.57-.12 0-.23-.02-.3-.03-.01-.06-.04-.22-.04-.39 0-1.15.572-2.27 1.206-2.98.804-.94 2.142-1.64 3.248-1.68.03.13.05.28.05.43zm4.565 15.71c-.03.07-.463 1.58-1.518 3.12-.945 1.34-1.94 2.71-3.43 2.71-1.517 0-1.9-.88-3.63-.88-1.698 0-2.302.91-3.67.91-1.377 0-2.332-1.26-3.428-2.8-1.287-1.82-2.323-4.63-2.323-7.28 0-4.28 2.797-6.55 5.552-6.55 1.448 0 2.675.95 3.6.95.865 0 2.222-1.01 3.902-1.01.613 0 2.886.06 4.374 2.19-.13.09-2.383 1.37-2.383 4.19 0 3.26 2.854 4.42 2.955 4.45z" />
        </svg>
      );
    case "windows":
      return (
        <svg viewBox="0 0 24 24" className={className} aria-label="Windows">
          <path fill="#0078D4" d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801" />
        </svg>
      );
    case "android":
      return (
        <svg viewBox="0 0 24 24" className={className} fill="#3DDC84" aria-label="Android">
          <path d="M17.523 15.34a1.0 1.0 0 11.998-1.0 1.0 1.0 0 01-.998 1.0m-11.046 0a1.0 1.0 0 11.998-1.0 1.0 1.0 0 01-.998 1.0m11.405-6.02l1.997-3.46a.416.416 0 00-.152-.567.416.416 0 00-.568.152l-2.022 3.503A12.06 12.06 0 0012 7.5c-1.8 0-3.51.38-5.135 1.087L4.843 5.084a.416.416 0 00-.568-.152.416.416 0 00-.152.567l1.997 3.46C2.688 11.123.524 14.51.04 18.5h23.92c-.484-3.99-2.648-7.377-6.078-9.18" />
        </svg>
      );
    case "linux":
      return (
        <svg viewBox="0 0 24 24" className={className} fill="#FCC624" aria-label="Linux">
          <path d="M12.504 0c-.155 0-.315.008-.480.021-4.226.333-3.105 4.807-3.17 6.298-.076 1.092-.3 1.953-1.05 3.02-.885 1.051-2.127 2.75-2.716 4.521-.278.832-.41 1.684-.287 2.489a.424.424 0 00-.11.135c-.26.268-.45.6-.663.839-.199.199-.485.267-.797.4-.313.136-.658.269-.864.68-.09.189-.136.394-.132.602 0 .199.027.4.055.6.058.399.116.728.04.97-.249.68-.28 1.145-.106 1.484.174.334.535.47.94.601.81.2 1.91.135 2.774.6.926.466 1.866.67 2.616.47.526-.116.97-.464 1.208-.946.587-.003 1.23-.269 2.26-.334.699-.058 1.574.267 2.577.2.025.134.063.198.114.333l.003.003c.391.778 1.113 1.132 1.884 1.071.771-.06 1.592-.536 2.257-1.306.631-.765 1.683-1.084 2.378-1.503.348-.199.629-.469.649-.853.023-.4-.2-.811-.714-1.376v-.097l-.003-.003c-.17-.2-.25-.535-.338-.926-.085-.401-.182-.786-.492-1.046h-.003c-.059-.054-.123-.067-.188-.135a.357.357 0 00-.19-.064c.431-1.278.264-2.55-.173-3.694-.533-1.41-1.465-2.638-2.175-3.483-.796-1.005-1.576-1.957-1.56-3.368.026-2.152.236-6.133-3.544-6.139z" />
        </svg>
      );
    default:
      return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" aria-label="Device">
          <rect x="2" y="3" width="20" height="14" rx="2" />
          <path d="M8 21h8M12 17v4" />
        </svg>
      );
  }
}
