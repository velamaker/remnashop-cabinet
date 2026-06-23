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
  isAdmin: boolean;
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
  const [hasPassword, setHasPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const refreshMe = useCallback(async () => {
    try {
      const me = await authApi.me();
      setUser(me);
      // fail-closed: админ-доступ только при явном is_admin от бэкенда
      try {
        const who = await authApi.whoami();
        setIsAdmin(Boolean(who?.is_admin));
        setHasPassword(Boolean(who?.has_password));
      } catch {
        setIsAdmin(false);
        setHasPassword(false);
      }
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        setUser(null);
        setIsAdmin(false);
        setHasPassword(false);
      } else {
        throw e;
      }
    }
  }, []);

  useEffect(() => {
    refreshMe().finally(() => setIsLoading(false));
  }, [refreshMe]);

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
      setHasPassword(false);
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      isAdmin,
      hasPassword,
      isLoading,
      login,
      register,
      loginWithTelegram,
      loginWithTelegramWebApp,
      logout,
      refreshMe,
    }),
    [user, isAdmin, hasPassword, isLoading, login, register, loginWithTelegram, loginWithTelegramWebApp, logout, refreshMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
