import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { BrandingProvider } from "@/contexts/BrandingContext";
import { AuthProvider } from "@/contexts/AuthContext";
import { ProtectedRoute, PublicOnlyRoute } from "@/components/ProtectedRoute";
import { AdminRoute } from "@/components/AdminRoute";
import { MaintenanceGate } from "@/components/MaintenanceGate";
import { I18nProvider } from "@/i18n/I18nContext";

import LoginPage from "@/pages/LoginPage";
import RegisterPage from "@/pages/RegisterPage";
import ResetPasswordPage from "@/pages/ResetPasswordPage";
import StatusPage from "@/pages/StatusPage";
import PricingPage from "@/pages/PricingPage";
import DashboardPage from "@/pages/DashboardPage";
import DevicesPage from "@/pages/DevicesPage";
import BillingPage from "@/pages/BillingPage";
import SettingsPage from "@/pages/SettingsPage";
import ReferralPage from "@/pages/ReferralPage";
import BalancePage from "@/pages/BalancePage";
import InfoPage from "@/pages/InfoPage";

import AdminHomePage from "@/pages/admin/AdminHomePage";
import AdminDashboardPage from "@/pages/admin/AdminDashboardPage";
import AdminUsersPage from "@/pages/admin/AdminUsersPage";
import AdminTransactionsPage from "@/pages/admin/AdminTransactionsPage";
import AdminPromocodesPage from "@/pages/admin/AdminPromocodesPage";
import AdminPlansPage from "@/pages/admin/AdminPlansPage";
import AdminGatewaysPage from "@/pages/admin/AdminGatewaysPage";
import AdminAdLinksPage from "@/pages/admin/AdminAdLinksPage";
import AdminBroadcastsPage from "@/pages/admin/AdminBroadcastsPage";
import AdminSettingsPage from "@/pages/admin/AdminSettingsPage";
import AdminTopupPage from "@/pages/admin/AdminTopupPage";
import AdminMorningSummaryPage from "@/pages/admin/AdminMorningSummaryPage";
import AdminTrialDiscountPage from "@/pages/admin/AdminTrialDiscountPage";
import AdminWinbackPage from "@/pages/admin/AdminWinbackPage";
import AdminPromoBannerPage from "@/pages/admin/AdminPromoBannerPage";
import AdminDigestPage from "@/pages/admin/AdminDigestPage";
import AdminReservePage from "@/pages/admin/AdminReservePage";
import AdminTrafficAlertPage from "@/pages/admin/AdminTrafficAlertPage";
import AdminNewDevicePage from "@/pages/admin/AdminNewDevicePage";
import AdminFreezePage from "@/pages/admin/AdminFreezePage";
import AdminBackupPage from "@/pages/admin/AdminBackupPage";
import AdminIpPage from "@/pages/admin/AdminIpPage";
import AdminServerStatusPage from "@/pages/admin/AdminServerStatusPage";
import AdminSubscriptionAppPage from "@/pages/admin/AdminSubscriptionAppPage";
import AdminAppearancePage from "@/pages/admin/AdminAppearancePage";
import AdminCabinetPage from "@/pages/admin/AdminCabinetPage";
import AdminInfoPage from "@/pages/admin/AdminInfoPage";
import AdminRemnaWavePage from "@/pages/admin/AdminRemnaWavePage";
import AdminSupportPage from "@/pages/admin/AdminSupportPage";
import AdminAuditPage from "@/pages/admin/AdminAuditPage";
import AdminAbusePage from "@/pages/admin/AdminAbusePage";
import AdminImportPage from "@/pages/admin/AdminImportPage";
import AdminReferralPage from "@/pages/admin/AdminReferralPage";
import AdminUpdatesPage from "@/pages/admin/AdminUpdatesPage";
import AdminNotificationsPage from "@/pages/admin/AdminNotificationsPage";
import AdminAppsPage from "@/pages/admin/AdminAppsPage";
import AdminMenuPage from "@/pages/admin/AdminMenuPage";
import AdminEmailPage from "@/pages/admin/AdminEmailPage";
import AdminAuthPage from "@/pages/admin/AdminAuthPage";
import SupportPage from "@/pages/SupportPage";
import RootPage from "@/pages/RootPage";

export default function App() {
  return (
    <I18nProvider>
    <ThemeProvider>
      <BrandingProvider>
      <AuthProvider>
        <BrowserRouter>
          <MaintenanceGate>
          <Routes>
            {/* Публичная страница статуса — без входа */}
            <Route path="/status" element={<StatusPage />} />
            <Route path="/pricing" element={<PricingPage />} />
            <Route path="/plans" element={<PricingPage />} />
            <Route
              path="/login"
              element={
                <PublicOnlyRoute>
                  <LoginPage />
                </PublicOnlyRoute>
              }
            />
            <Route
              path="/register"
              element={
                <PublicOnlyRoute>
                  <RegisterPage />
                </PublicOnlyRoute>
              }
            />
            <Route
              path="/reset-password"
              element={
                <PublicOnlyRoute>
                  <ResetPasswordPage />
                </PublicOnlyRoute>
              }
            />

            <Route path="/" element={<RootPage />} />
            <Route
              path="/subscription"
              element={
                <ProtectedRoute>
                  <DashboardPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/devices"
              element={
                <ProtectedRoute>
                  <DevicesPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/billing"
              element={
                <ProtectedRoute>
                  <BillingPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/referral"
              element={
                <ProtectedRoute>
                  <ReferralPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <ProtectedRoute>
                  <SettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/balance"
              element={
                <ProtectedRoute>
                  <BalancePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/support"
              element={
                <ProtectedRoute>
                  <SupportPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/info"
              element={
                <ProtectedRoute>
                  <InfoPage />
                </ProtectedRoute>
              }
            />

            {/* Admin panel */}
            <Route
              path="/admin"
              element={
                <AdminRoute>
                  <AdminHomePage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/stats"
              element={
                <AdminRoute>
                  <AdminDashboardPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/users"
              element={
                <AdminRoute>
                  <AdminUsersPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/transactions"
              element={
                <AdminRoute>
                  <AdminTransactionsPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/promocodes"
              element={
                <AdminRoute>
                  <AdminPromocodesPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/plans"
              element={
                <AdminRoute>
                  <AdminPlansPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/gateways"
              element={
                <AdminRoute>
                  <AdminGatewaysPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/ad-links"
              element={
                <AdminRoute>
                  <AdminAdLinksPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/broadcasts"
              element={
                <AdminRoute>
                  <AdminBroadcastsPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/settings"
              element={
                <AdminRoute>
                  <AdminSettingsPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/topup"
              element={
                <AdminRoute>
                  <AdminTopupPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/summary"
              element={
                <AdminRoute>
                  <AdminMorningSummaryPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/trial-discount"
              element={
                <AdminRoute>
                  <AdminTrialDiscountPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/winback"
              element={
                <AdminRoute>
                  <AdminWinbackPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/promo-banner"
              element={
                <AdminRoute>
                  <AdminPromoBannerPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/digest"
              element={
                <AdminRoute>
                  <AdminDigestPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/reserve"
              element={
                <AdminRoute>
                  <AdminReservePage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/traffic-alert"
              element={
                <AdminRoute>
                  <AdminTrafficAlertPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/admin-ip"
              element={
                <AdminRoute>
                  <AdminIpPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/backup"
              element={
                <AdminRoute>
                  <AdminBackupPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/freeze"
              element={
                <AdminRoute>
                  <AdminFreezePage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/new-device"
              element={
                <AdminRoute>
                  <AdminNewDevicePage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/server-status"
              element={
                <AdminRoute>
                  <AdminServerStatusPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/subscription-app"
              element={
                <AdminRoute>
                  <AdminSubscriptionAppPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/appearance"
              element={
                <AdminRoute>
                  <AdminAppearancePage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/cabinet"
              element={
                <AdminRoute>
                  <AdminCabinetPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/info"
              element={
                <AdminRoute>
                  <AdminInfoPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/apps"
              element={
                <AdminRoute>
                  <AdminAppsPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/menu"
              element={
                <AdminRoute>
                  <AdminMenuPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/email"
              element={
                <AdminRoute>
                  <AdminEmailPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/auth"
              element={
                <AdminRoute>
                  <AdminAuthPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/audit"
              element={
                <AdminRoute>
                  <AdminAuditPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/abuse"
              element={
                <AdminRoute>
                  <AdminAbusePage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/import"
              element={
                <AdminRoute>
                  <AdminImportPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/referral"
              element={
                <AdminRoute>
                  <AdminReferralPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/updates"
              element={
                <AdminRoute>
                  <AdminUpdatesPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/notifications"
              element={
                <AdminRoute>
                  <AdminNotificationsPage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/remnawave"
              element={
                <AdminRoute>
                  <AdminRemnaWavePage />
                </AdminRoute>
              }
            />
            <Route
              path="/admin/support"
              element={
                <AdminRoute>
                  <AdminSupportPage />
                </AdminRoute>
              }
            />
          </Routes>
          </MaintenanceGate>
        </BrowserRouter>
      </AuthProvider>
      </BrandingProvider>
    </ThemeProvider>
    </I18nProvider>
  );
}
