import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";

// One-time migration: evict all Okta token keys that were previously stored in
// localStorage (storage was changed to sessionStorage). Without this, returning
// users would still have the stale localStorage tokens and the Okta SDK would
// try to use them, causing silent auth failures and 401s on every request.
const OKTA_LS_MIGRATION_KEY = 'okta_ls_migration_v1';
if (!localStorage.getItem(OKTA_LS_MIGRATION_KEY)) {
  Object.keys(localStorage)
    .filter(k => k.startsWith('okta-') || k.startsWith('okta'))
    .forEach(k => localStorage.removeItem(k));
  localStorage.setItem(OKTA_LS_MIGRATION_KEY, '1');
}

createRoot(document.getElementById("root")!).render(
  // <StrictMode>
  <App />
  // </StrictMode>
);
