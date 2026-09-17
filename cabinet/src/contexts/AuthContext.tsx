import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { authApi } from "@/api/auth";
import { referralApi } from "@/api/referral";
import { captureReferralCode, clearReferralCode, readReferralCode } from "@/lib/referralRef";
import { BOT_PAGE_NOTE, pageBotReady } from "@/lib/botCapabilities";
import { useBranding } from "@/contexts/BrandingContext";
import { ApiError } from "@/types/api";
import type {
  LoginRequest,
  MeResponse,
  RegisterRequest,
  TelegramAuthRequest,
  TelegramWebAppAuthRequest,
} from "@/types/api";

interface AuthContextValue {
  user: MeResponse | null;
  // Доступ к админ-разделу вообще (полный админ ИЛИ read-only).
  isAdmin: boolean;
  // Админ только для просмотра — видит админку, но ничего не меняет.
  isReadonlyAdmin: boolean;
  // Владелец — может менять роли пользователей.
  isOwner: boolean;
  // Полный доступ ко всем разделам админки.
  fullAccess: boolean;
  // Ключи разрешённых разделов (при неполном доступе). Пусто = только те, что в grant.
  sections: string[];
  /** Доступные страницы админки; пусто = ограничения нет (см. canPage). */
  pages: string[] | null;
  /** Страницы, где бэкенду нечего сохранять: поля показываем, кнопки — нет. */
  readonlyPages: string[];
  /** Страница только для чтения (бэкенд не умеет там писать). */
  isPageReadonly: (path: string) => boolean;
  // Разрешён ли раздел (полный доступ ИЛИ ключ в списке).
  canSection: (key: string) => boolean;
  /** Доступна ли страница админки по её адресу. Бэкенд не прислал список — доступна. */
  canPage: (path: string) => boolean;
  /** Почему страницы здесь нет — если бэкенд назвал причину. Не назвал — undefined. */
  pageNote: (path: string) => string | undefined;
  hasPassword: boolean;
  isLoading: boolean;
  login: (data: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  loginWithTelegram: (data: TelegramAuthRequest) => Promise<void>;
  loginWithTelegramWebApp: (data: TelegramWebAppAuthRequest) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [isReadonlyAdmin, setIsReadonlyAdmin] = useState(false);
  const [isOwner, setIsOwner] = useState(false);
  const [fullAccess, setFullAccess] = useState(false);
  const [sections, setSections] = useState<string[]>([]);
  const [pages, setPages] = useState<string[] | null>(null);
  const [readonlyPages, setReadonlyPages] = useState<string[]>([]);
  const [pageNotes, setPageNotes] = useState<Record<string, string>>({});
  const [hasPassword, setHasPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  // AuthProvider живёт внутри BrandingProvider (App.tsx): оформление нужно, чтобы
  // знать, какие страницы админки умеет работающий бот.
  const { appearance } = useBranding();

  const refreshMe = useCallback(async () => {
    try {
      const me = await authApi.me();
      setUser(me);
      // fail-closed: админ-доступ только при явных флагах от бэкенда
      try {
        const who = await authApi.whoami();
        // В админ-раздел пускаем как полных, так и read-only админов.
        setIsAdmin(Boolean(who?.can_access_admin ?? who?.is_admin));
        setIsReadonlyAdmin(Boolean(who?.is_readonly_admin));
        setIsOwner(Boolean(who?.is_owner));
        setFullAccess(Boolean(who?.full_access));
        setSections(Array.isArray(who?.sections) ? who.sections : []);
        // Список страниц необязателен: его шлёт только бэкенд, умеющий не всё.
        setPages(Array.isArray(who?.pages) ? who.pages : null);
        // Поля нет вовсе (наш собственный бэкенд) = сохранять можно везде.
        setReadonlyPages(Array.isArray(who?.readonly_pages) ? who.readonly_pages : []);
        // Тоже необязательное поле: причины шлёт только бэкенд, который чего-то
        // намеренно не делает. Нет поля — нет и причины, экран прежний.
        setPageNotes(
          who?.page_notes && typeof who.page_notes === "object" ? who.page_notes : {},
        );
        setHasPassword(Boolean(who?.has_password));
      } catch {
        setIsAdmin(false);
        setIsReadonlyAdmin(false);
        setIsOwner(false);
        setFullAccess(false);
        setSections([]);
        setHasPassword(false);
      }
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        setUser(null);
        setIsAdmin(false);
        setIsReadonlyAdmin(false);
        setIsOwner(false);
        setHasPassword(false);
      } else {
        throw e;
      }
    }
  }, []);

  useEffect(() => {
    // Код из ссылки приглашения снимаем ДО входа: адрес со временем сменится
    // (редирект OIDC, переход на страницу входа), а код должен пережить это.
    captureReferralCode();
    refreshMe().finally(() => setIsLoading(false));
  }, [refreshMe]);

  // Досчёт приглашения. Ждём именно ПОЯВЛЕНИЯ пользователя, а не конкретной
  // кнопки входа: так одинаково отрабатывают виджет Telegram, мини-приложение,
  // возврат с oauth.telegram.org и обычная регистрация. Повторы безопасны —
  // сервер откажет, если пригласивший уже есть.
  useEffect(() => {
    if (!user) return;
    const code = readReferralCode();
    if (!code) return;
    clearReferralCode();
    referralApi.attach(code).catch(() => {
      // Приглашение не засчиталось — это не повод мешать человеку пользоваться
      // кабинетом. Разбор — в логах сервера.
    });
  }, [user]);

  const login = useCallback(
    async (data: LoginRequest) => {
      await authApi.login(data);
      await refreshMe();
    },
    [refreshMe],
  );

  const register = useCallback(
    async (data: RegisterRequest) => {
      await authApi.register(data);
      await refreshMe();
    },
    [refreshMe],
  );

  const loginWithTelegram = useCallback(
    async (data: TelegramAuthRequest) => {
      await authApi.telegramLogin(data);
      await refreshMe();
    },
    [refreshMe],
  );

  const loginWithTelegramWebApp = useCallback(
    async (data: TelegramWebAppAuthRequest) => {
      await authApi.telegramWebAppLogin(data);
      await refreshMe();
    },
    [refreshMe],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
      setIsAdmin(false);
      setIsReadonlyAdmin(false);
      setIsOwner(false);
      setFullAccess(false);
      setSections([]);
      setHasPassword(false);
    }
  }, []);

  const canSection = useCallback(
    (key: string) => fullAccess || sections.includes(key),
    [fullAccess, sections],
  );
  // Права проверяет canSection, а это — про УМЕНИЕ бэкенда: страницы, которых он
  // не поддерживает, прятать даже у владельца с полным доступом. Два источника:
  // список страниц чужого бэкенда (whoami.pages) и возможности нашего бота — кабинет
  // бывает новее бота, и тогда раздел, которому нужен новый бот, открывался бы с
  // «Не удалось загрузить». Одна проверка закрывает меню, плитки и прямой адрес.
  const canPage = useCallback(
    (path: string) => (pages === null || pages.includes(path)) && pageBotReady(appearance, path),
    [pages, appearance],
  );

  // Отдельно от canSection: там про ПРАВА админа, здесь — про умение бэкенда.
  // Условия пополнения, например, у «Бедолаги» собираются из настроек каждого
  // шлюза, и общего места для записи нет — кнопка «Сохранить» там только врала бы.
  const isPageReadonly = useCallback(
    (path: string) => readonlyPages.includes(path),
    [readonlyPages],
  );

  // Причина, по которой страницы здесь нет. Показывается только тому, кто всё
  // же пришёл по адресу руками: в меню такой страницы нет вовсе.
  // Причина от чужого бэкенда главнее; своя — только когда не хватает бота.
  const pageNote = useCallback(
    (path: string) => pageNotes[path] ?? (pageBotReady(appearance, path) ? undefined : BOT_PAGE_NOTE),
    [pageNotes, appearance],
  );

  const value = useMemo(
    () => ({
      user,
      isAdmin,
      isReadonlyAdmin,
      isOwner,
      fullAccess,
      sections,
      pages,
      readonlyPages,
      isPageReadonly,
      canPage,
      pageNote,
      canSection,
      hasPassword,
      isLoading,
      login,
      register,
      loginWithTelegram,
      loginWithTelegramWebApp,
      logout,
      refreshMe,
    }),
    [user, isAdmin, isReadonlyAdmin, isOwner, fullAccess, sections, pages, readonlyPages, isPageReadonly, canPage, pageNote, canSection, hasPassword, isLoading, login, register, loginWithTelegram, loginWithTelegramWebApp, logout, refreshMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
