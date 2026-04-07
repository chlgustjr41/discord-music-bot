import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { doc, getDoc } from "firebase/firestore";
import { db } from "../firebase";

export function EntryScreen() {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleConnect = async () => {
    if (!code.trim()) return;
    setLoading(true);
    setError("");

    const codeDoc = await getDoc(doc(db, "sessionCodes", code.toUpperCase()));
    if (!codeDoc.exists()) {
      setError("Invalid or expired session code.");
      setLoading(false);
      return;
    }

    navigate(`/dashboard/${code.toUpperCase()}`);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "24px" }}>
      <h1>Jacky Music</h1>
      <p>Enter your session code to access the playlist</p>

      <div style={{ display: "flex", gap: "8px" }}>
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="Enter session code"
          maxLength={6}
          style={{ fontSize: "1.5rem", textAlign: "center", width: "200px", letterSpacing: "4px" }}
          onKeyDown={(e) => e.key === "Enter" && handleConnect()}
        />
        <button onClick={handleConnect} disabled={loading}>
          {loading ? "Connecting..." : "Connect"}
        </button>
      </div>

      {error && <p style={{ color: "red" }}>{error}</p>}

      <hr style={{ width: "300px", margin: "16px 0" }} />

      <button onClick={() => navigate("/activate")} style={{ opacity: 0.7 }}>
        Server Owner? Activate Your Server
      </button>
    </div>
  );
}
