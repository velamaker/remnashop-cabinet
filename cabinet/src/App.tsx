import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { AuthProvider } from "@/contexts/AuthContext";
import { ProtectedRoute, PublicOnlyRoute } from "@/components/ProtectedRoute";
import { AdminRoute } from "@/components/AdminRoute";

import LoginPage from "@/pages/LoginPage";
import RegisterPage from "@/pages/RegisterPage";
import DashboardPage from "@/pages/DashboardPage";
import DevicesPage from "@/pages/DevicesPage";
import BillingPage from "@/pages/BillingPage";
import SettingsPage from "@/pages/SettingsPage";
import ReferralPage from "@/pages/ReferralPage";
import BalancePage from "@/pages/BalancePage";
import InfoPage from "@/pages/InfoPage";

import AdminDashboardPage from "@/pages/admin/AdminDashboardPage";
import AdminUsersPage from "@/pages/admin/AdminUsersPage";
import AdminTransactionsPage from "@/pages/admin/AdminTransactionsPage";
import AdminPromocodesPage from "@/pages/admin/AdminPromocodesPage";
import AdminPlansPage from "@/pages/admin/AdminPlansPage";
import AdminGatewaysPage from "@/pages/admin/AdminGatewaysPage";
import AdminAdLinksPage from "@/pages/admin/AdminAdLinksPage";
import AdminBroadcastsPage from "@/pages/admin/AdminBroadcastsPage";
import AdminSettingsPage from "@/pages/admin/AdminSettingsPage";
import AdminRemnaWavePage from "@/pages/admin/AdminRemnaWavePage";
import AdminSupportPage from "@/pages/admin/AdminSupportPage";
import SupportPage from "@/pages/SupportPage";
import HomePage from "@/pages/HomePage";

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
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
              path="/"
              element={
                <ProtectedRoute>
                  <HomePage />
                </ProtectedRoute>
              }
            />
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
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  );
}
