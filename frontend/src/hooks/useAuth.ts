import { useState, useEffect } from "react";
import { onAuthStateChanged, signInWithPopup, signOut } from "firebase/auth";
import type { User } from "firebase/auth";
import { auth, googleProvider } from "../firebase";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  const signInWithGoogle = async () => {
    setError(null);
    try {
      await signInWithPopup(auth, googleProvider);
    } catch (err: any) {
      const code = err?.code || "";
      if (code === "auth/popup-closed-by-user") {
        setError("Sign-in popup was closed. Please try again.");
      } else if (code === "auth/unauthorized-domain") {
        setError(
          "This domain is not authorized for sign-in. Add it to Firebase Console → Authentication → Settings → Authorized domains."
        );
      } else {
        setError(err?.message || "Sign-in failed. Please try again.");
      }
    }
  };

  const logout = () => signOut(auth);

  return { user, loading, error, signInWithGoogle, logout };
}
