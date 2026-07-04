import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card } from "@/components/ui/Card";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { ApiError } from "@/types/api";

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [referralCode] = useState(searchParams.get("ref") || "");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      await register({
        email,
        password,
        name: name || undefined,
        referral_code: referralCode || undefined,
      });
      navigate("/");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Не удалось зарегистрироваться.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="app-scroll bg-grain flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="absolute right-4 top-4">
        <ThemeSwitcher />
      </div>

      <Card className="w-full max-w-sm animate-fade-in">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-accent text-accent-fg shadow-glow">
            <span className="text-base font-bold">R</span>
          </div>
          <h1 className="text-lg font-semibold text-fg">Создать аккаунт</h1>
          <p className="mt-1 text-sm text-fg-subtle">Это займёт меньше минуты</p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Input
            label="Имя"
            name="name"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            label="Email"
            type="email"
            name="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Input
            label="Пароль"
            type="password"
            name="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />

          {error && <p className="text-sm text-danger">{error}</p>}

          <Button type="submit" isLoading={isLoading} className="mt-1 w-full">
            Зарегистрироваться
          </Button>
        </form>

        <p className="mt-5 text-center text-sm text-fg-subtle">
          Уже есть аккаунт?{" "}
          <Link to="/login" className="font-medium text-accent hover:text-accent-hover">
            Войти
          </Link>
        </p>
      </Card>
    </div>
  );
}
