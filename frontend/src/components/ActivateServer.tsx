import { useState } from "react";
import { doc, setDoc, serverTimestamp } from "firebase/firestore";
import { db } from "../firebase";
import { useAuth } from "../hooks/useAuth";
import { useNavigate } from "react-router-dom";

export function ActivateServer() {
  const { user, loading: authLoading, signInWithGoogle } = useAuth();
  const [serverId, setServerId] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "done" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const navigate = useNavigate();

  const handleActivate = async () => {
    if (!serverId.trim() || !user) return;
    setStatus("saving");
    setErrorMsg("");

    try {
      await setDoc(doc(db, "serverOwners", serverId.trim()), {
        ownerDiscordId: "",  // User fills in manually or via bot linking later
        ownerEmail: user.email,
        firebaseUid: user.uid,
        activatedAt: serverTimestamp(),
        isActive: true,
      });
      setStatus("done");
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to activate server.");
      setStatus("error");
    }
  };

  if (authLoading) return <p>Loading...</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "16px" }}>
      <h1>Activate Your Server</h1>

      {!user ? (
        <>
          <p>Sign in with Google to activate Jacky Music for your Discord server.</p>
          <button onClick={signInWithGoogle}>Sign in with Google</button>
        </>
      ) : status === "done" ? (
        <>
          <p>Server <strong>{serverId}</strong> has been activated!</p>
          <p>Jacky Music will now respond to commands in your server.</p>
          <button onClick={() => navigate("/")}>Back to Home</button>
        </>
      ) : (
        <>
          <p>Signed in as <strong>{user.email}</strong></p>
          <p>Enter your Discord Server ID:</p>
          <p style={{ fontSize: "0.85rem", opacity: 0.6 }}>
            (Right-click your server name in Discord → Copy Server ID. Enable Developer Mode in Discord settings if you don't see this option.)
          </p>
          <input
            type="text"
            value={serverId}
            onChange={(e) => setServerId(e.target.value)}
            placeholder="Discord Server ID"
            style={{ fontSize: "1.1rem", width: "280px" }}
          />
          <button onClick={handleActivate} disabled={status === "saving"}>
            {status === "saving" ? "Activating..." : "Activate Server"}
          </button>
          {errorMsg && <p style={{ color: "red" }}>{errorMsg}</p>}
        </>
      )}
    </div>
  );
}
