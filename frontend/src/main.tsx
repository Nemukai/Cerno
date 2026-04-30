import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AuthGate } from "./components/AuthGate";
import { BetaGate } from "./components/BetaGate";
import "./styles.css";

const container = document.getElementById("root");
if (!container) throw new Error("#root not found");

createRoot(container).render(
  <StrictMode>
    <AuthGate>
      {(user, _signOut, onUserUpdate) => (
        <BetaGate user={user} onUserUpdate={onUserUpdate}>
          <App />
        </BetaGate>
      )}
    </AuthGate>
  </StrictMode>,
);
