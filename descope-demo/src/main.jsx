// src/main.jsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { AuthProvider } from "@descope/react-sdk";

const projectId = import.meta.env.VITE_DESCOPE_PROJECT_ID;
if (!projectId) {
  console.warn("VITE_DESCOPE_PROJECT_ID is not set. The Descope SDK may not initialize correctly.");
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AuthProvider projectId={projectId || ""}>
      <App />
    </AuthProvider>
  </React.StrictMode>
);
