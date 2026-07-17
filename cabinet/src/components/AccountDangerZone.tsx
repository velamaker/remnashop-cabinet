import { useState } from "react";
import { Trash2, Loader2, ShieldAlert } from "lucide-react";
import { accountApi } from "@/api/account";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";

// Опасная зона: самоудаление аккаунта (право на удаление, 152-ФЗ/GDPR).
// Экспорт данных в JSON убран из UI — для VPN-кабинета обычному юзеру не нужен;
// эндпоинт /account/export остаётся для выдачи данных по запросу.

// Фраза подтверждения — БЭКЕНД проверяет её буквально (account.py /delete),
// поэтому НЕ переводим: пользователь всегда вводит именно «УДАЛИТЬ».
const CONFIRM_PHRASE = "УДАЛИТЬ";

export function AccountDangerZone() {
  const t = useT();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  const doDelete = async () => {
    setDeleting(true);
    setDeleteErr(null);
    try {
      await accountApi.delete(confirmText.trim());
      // Аккаунт удалён — уводим на вход.
      window.location.href = "/login";
    } catch (e) {
      setDeleteErr(e instanceof ApiError ? e.detail : t("account.dz.deleteErr"));
      setDeleting(false);
    }
  };

  return (
    <section className="rounded-2xl border border-danger/40 bg-danger/5 p-5">
      <div className="flex items-center gap-2">
        <ShieldAlert className="h-5 w-5 text-danger" />
        <h3 className="text-base font-bold text-fg">{t("account.dz.title")}</h3>
      </div>

      <div className="mt-4 space-y-3">
        {/* Удаление */}
        <div className="rounded-xl border border-danger/30 bg-bg px-3 py-3">
          <p className="text-sm font-medium text-fg">{t("account.dz.heading")}</p>
          <p className="text-xs text-fg-muted">{t("account.dz.desc")}</p>

          {!confirmOpen ? (
            <button
              type="button"
              onClick={() => setConfirmOpen(true)}
              className="mt-2.5 inline-flex items-center gap-1.5 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-sm font-medium text-danger hover:bg-danger/15"
            >
              <Trash2 className="h-4 w-4" />
              {t("account.dz.deleteBtn")}
            </button>
          ) : (
            <div className="mt-2.5 space-y-2">
              <label className="block text-xs text-fg-muted">
                {t("account.dz.confirmLabel", { phrase: CONFIRM_PHRASE })}
              </label>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                autoFocus
                className="w-full rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-danger"
                placeholder={CONFIRM_PHRASE}
              />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={doDelete}
                  disabled={deleting || confirmText.trim().toUpperCase() !== CONFIRM_PHRASE}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-danger px-3 py-2 text-sm font-semibold text-white hover:bg-danger/90 disabled:opacity-50"
                >
                  {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                  {t("account.dz.deleteForever")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirmOpen(false);
                    setConfirmText("");
                    setDeleteErr(null);
                  }}
                  disabled={deleting}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg-subtle/70 disabled:opacity-50"
                >
                  {t("account.dz.cancel")}
                </button>
              </div>
              {deleteErr && <p className="text-xs text-danger">{deleteErr}</p>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
