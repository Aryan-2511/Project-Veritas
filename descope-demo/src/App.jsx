// src/App.jsx
import React, { useState } from "react";
import { useDescope, useSession, useUser, Descope } from "@descope/react-sdk";

export default function App() {
  const { isAuthenticated, sessionToken } = useSession();
  const { user } = useUser();
  const descope = useDescope();

  const [delegateResponse, setDelegateResponse] = useState(null);
  const [loading, setLoading] = useState(false);

  const callConciergeDelegate = async () => {
    if (!sessionToken) {
      alert("No session token found — please sign in.");
      return;
    }

    setLoading(true);
    setDelegateResponse(null);
    try {
      // Use relative URL to let Vite proxy forward /delegate to backend,
      // or fetch directly to http://localhost:8080 if you prefer.
      const resp = await fetch("/delegate", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${sessionToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          target: "scout",
          scopes: ["data:read:arxiv","data:read:twitter"],
          expires_in: 300,
        }),
      });

      // Try parse JSON for error or success
      const data = await resp.json().catch(() => null);

      if (!resp.ok) {
        // Prefer `detail` if the backend returned an HTTPException
        const msg = (data && (data.detail || data.message)) || resp.statusText || "Unknown error";
        throw new Error(msg);
      }

      setDelegateResponse(data);
    } catch (err) {
      console.error("Delegate error", err);
      alert(`Delegation error: ${err.message || err}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ fontFamily: "system-ui", padding: 20 }}>
      <h2>Veritas + Descope React Demo</h2>

      {!isAuthenticated ? (
        <>
          <p>Please log in:</p>
          <Descope flowId="sign-up-or-in" />
        </>
      ) : (
        <>
          <p>✅ Logged in as: <strong>{user?.name || user?.email || user?.loginId}</strong></p>
          <p>Session token: <code>{sessionToken ? `${sessionToken.slice(0,40)}...` : "none"}</code></p>

          <div style={{ marginTop: 12 }}>
            <button onClick={callConciergeDelegate} disabled={loading}>
              {loading ? "Requesting delegated token..." : "Get Delegated Scout Token"}
            </button>
            <button style={{ marginLeft: 12 }} onClick={() => descope.logout()}>
              Logout
            </button>
          </div>

          {delegateResponse && (
            <section style={{ marginTop: 18 }}>
              <h3>Delegated Token Response</h3>
              <pre style={{ background: "#f7f7f7", padding: 10 }}>
                {JSON.stringify(delegateResponse, null, 2)}
              </pre>
            </section>
          )}
        </>
      )}
    </div>
  );
}
