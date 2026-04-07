import { useState, useEffect } from "react";
import { onAuthStateChanged, User, signInWithPopup, signOut } from "firebase/auth";
import { auth, googleProvider } from "../firebase";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  const signInWithGoogle = () => signInWithPopup(auth, googleProvider);
  const logout = () => signOut(auth);

  return { user, loading, signInWithGoogle, logout };
}
