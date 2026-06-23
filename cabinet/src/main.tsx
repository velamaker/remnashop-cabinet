import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Self-hosted Inter — одинаковый шрифт на всех устройствах (без Google Fonts).
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/inter/800.css";
import "./index.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
