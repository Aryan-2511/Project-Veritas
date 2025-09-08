import React, { useState,} from "react";
import { useDescope, useSession, useUser, Descope } from "@descope/react-sdk";
import axios from "axios";

function App() {
  const { isAuthenticated, sessionToken } = useSession();
  const { user } = useUser();
  const descope = useDescope();

  const [delegateToken, setDelegateToken] = useState(null);
  // const [delegateExpiry, setDelegateExpiry] = useState(null);
  const [loading, setLoading] = useState(false);

  const [arxivInput, setArxivInput] = useState("");
  const [twitterInput, setTwitterInput] = useState("");
  const [subscriptions, setSubscriptions] = useState([]);
  const [message, setMessage] = useState("");

    // -------------------------
  // 1) Get Delegated Scout Token (Manual)
  // -------------------------
  const fetchDelegatedToken = async () => {
    if (!sessionToken) return;

    try {
      const resp = await fetch("http://localhost:8080/delegate", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${sessionToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          target: "scout",
          scopes: ["data:read:arxiv", "data:read:twitter", "moderation:perform"],
          expires_in: 300, // 5 min
        }),
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Failed to delegate");
      }

      const data = await resp.json();
      const token = data?.sessionToken?.jwt || data?.access_token || data?.jwt;
      if (!token) throw new Error("No token received");

      setDelegateToken(token);
      setMessage("✅ Delegated token created!");
    } catch (err) {
      console.error("Delegate error", err);
      alert("Failed to get delegated token: " + err.message);
    }
  };
  // -------------------------
  // Manual delegate button
  // -------------------------
  const callConciergeDelegate = async () => {
    setLoading(true);
    await fetchDelegatedToken();
    setLoading(false);
  };

  // -------------------------
  // Subscribe to Arxiv
  // -------------------------
  const handleSubscribeArxiv = async () => {
    if (!delegateToken) return alert("Get a delegated token first.");
    if (!arxivInput.trim()) return alert("Enter an Arxiv topic/id.");

    try {
      await axios.post(
        "http://localhost:8001/subscribe",
        {
          user_id: user?.userId || user?.email,
          source: "arxiv",
          url: arxivInput.trim(),
        },
        {
          headers: { Authorization: `Bearer ${delegateToken}` },
        }
      );
      setMessage("✅ Subscribed to Arxiv successfully!");
      setArxivInput("");
    } catch (err) {
      console.error("Arxiv subscription failed", err);
      alert("Failed Arxiv subscription: " + (err.response?.data?.detail || err.message));
    }
  };

  // -------------------------
  // Subscribe to Twitter
  // -------------------------
  const handleSubscribeTwitter = async () => {
    if (!delegateToken) return alert("Get a delegated token first.");
    if (!twitterInput.trim()) return alert("Enter a Twitter handle.");

    try {
      await axios.post(
        "http://localhost:8001/subscribe",
        {
          user_id: user?.userId || user?.email,
          source: "twitter",
          url: twitterInput.trim(),
        },
        {
          headers: { Authorization: `Bearer ${delegateToken}` },
        }
      );
      setMessage("✅ Subscribed to Twitter successfully!");
      setTwitterInput("");
    } catch (err) {
      console.error("Twitter subscription failed", err);
      alert("Failed Twitter subscription: " + (err.response?.data?.detail || err.message));
    }
  };

  // -------------------------
  // Show Subscriptions
  // -------------------------
  const handleShowSubscriptions = async () => {
    if (!delegateToken) return alert("Get a delegated token first.");

    try {
      const resp = await axios.get("http://localhost:8001/subscriptions", {
        headers: { Authorization: `Bearer ${delegateToken}` },
      });
      setSubscriptions(resp.data);
      setMessage(`✅ Fetched ${resp.data.length} subscriptions.`);
    } catch (err) {
      console.error("Fetch subscriptions failed", err);
      alert("Failed to fetch subscriptions: " + (err.response?.data?.detail || err.message));
    }
  };

  // -------------------------
  // Styles
  // -------------------------
  const containerStyle = {
    fontFamily: "system-ui",
    maxWidth: "700px",
    margin: "2rem auto",
    padding: "1.5rem",
    borderRadius: "12px",
    boxShadow: "0 5px 15px rgba(0,0,0,0.3)",
    backgroundColor: "#1f2937", // Dark background
    color: "#f3f4f6", // Light text
    transition: "background-color 0.3s ease, color 0.3s ease",
  };

  const cardStyle = {
    padding: "1rem",
    marginBottom: "1rem",
    borderRadius: "10px",
    backgroundColor: "#374151",
    boxShadow: "0 2px 8px rgba(0,0,0,0.5)",
    transition: "background-color 0.3s ease, transform 0.2s ease",
  };

  const inputStyle = {
    padding: "0.5rem",
    width: "calc(100% - 12px)",
    marginBottom: "0.5rem",
    borderRadius: "6px",
    border: "1px solid #6b7280",
    backgroundColor: "#1f2937",
    color: "#f3f4f6",
    transition: "border-color 0.3s ease, background-color 0.3s ease",
  };

  const buttonStyle = {
    padding: "0.5rem 1rem",
    border: "none",
    borderRadius: "6px",
    backgroundColor: "#4f46e5",
    color: "#fff",
    cursor: "pointer",
    marginBottom: "0.5rem",
    transition: "background-color 0.3s ease, transform 0.2s ease",
  };

  const buttonSecondaryStyle = { ...buttonStyle, backgroundColor: "#10b981" };
  const logoutStyle = { ...buttonStyle, backgroundColor: "#ef4444" };

  // Hover handlers
  const handleHover = (e) => (e.currentTarget.style.transform = "scale(1.05)");
  const handleLeave = (e) => (e.currentTarget.style.transform = "scale(1)");

  const handleInputFocus = (e) => (e.currentTarget.style.borderColor = "#4f46e5");
  const handleInputBlur = (e) => (e.currentTarget.style.borderColor = "#6b7280");

  return (
    <div style={containerStyle}>
      <h2>Veritas + Descope Demo</h2>

      {!isAuthenticated ? (
        <>
          <p>Please log in:</p>
          <Descope flowId="sign-up-or-in" />
        </>
      ) : (
        <>
          <p>
            ✅ Logged in as: <b>{user?.name || user?.email || user?.userId}</b>
          </p>

          <button
            style={buttonStyle}
            onMouseEnter={handleHover}
            onMouseLeave={handleLeave}
            onClick={callConciergeDelegate}
            disabled={loading}
          >
            {loading ? "Requesting delegated token..." : "Get Delegated Scout Token"}
          </button>

          {message && (
            <p style={{ marginTop: "0.5rem", color: "#34d399" }}>{message}</p>
          )}

          <hr style={{ borderColor: "#6b7280" }} />

          <div style={cardStyle}>
            <h3>Subscribe to Arxiv</h3>
            <input
              style={inputStyle}
              placeholder="Enter Arxiv topic (e.g. cs.AI)"
              value={arxivInput}
              onChange={(e) => setArxivInput(e.target.value)}
              onFocus={handleInputFocus}
              onBlur={handleInputBlur}
            />
            <button
              style={buttonSecondaryStyle}
              onMouseEnter={handleHover}
              onMouseLeave={handleLeave}
              onClick={handleSubscribeArxiv}
            >
              Subscribe Arxiv
            </button>
          </div>

          <div style={cardStyle}>
            <h3>Subscribe to Twitter</h3>
            <input
              style={inputStyle}
              placeholder="Enter Twitter handle"
              value={twitterInput}
              onChange={(e) => setTwitterInput(e.target.value)}
              onFocus={handleInputFocus}
              onBlur={handleInputBlur}
            />
            <button
              style={buttonSecondaryStyle}
              onMouseEnter={handleHover}
              onMouseLeave={handleLeave}
              onClick={handleSubscribeTwitter}
            >
              Subscribe Twitter
            </button>
          </div>

          <button
            style={buttonStyle}
            onMouseEnter={handleHover}
            onMouseLeave={handleLeave}
            onClick={handleShowSubscriptions}
          >
            Show Subscriptions
          </button>

          {subscriptions.length > 0 && (
            <ul>
              {subscriptions.map((s) => (
                <li key={s.id}>
                  <b>{s.source}</b>: {s.url} (id: {s.id})
                </li>
              ))}
            </ul>
          )}

          <hr style={{ borderColor: "#6b7280" }} />

          <button
            style={logoutStyle}
            onMouseEnter={handleHover}
            onMouseLeave={handleLeave}
            onClick={() => descope.logout()}
          >
            Logout
          </button>
        </>
      )}
    </div>
  );
}

export default App;
