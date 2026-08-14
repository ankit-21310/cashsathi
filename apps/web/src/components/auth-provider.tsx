"use client";

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  createUserWithEmailAndPassword,
  GoogleAuthProvider,
  onIdTokenChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as firebaseSignOut,
  User,
} from "firebase/auth";

import { getFirebaseAuth } from "@/lib/firebase";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  configurationError: string | null;
  signInWithGoogle: () => Promise<void>;
  signInWithEmail: (email: string, password: string) => Promise<void>;
  createEmailAccount: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [configurationError, setConfigurationError] = useState<string | null>(null);

  useEffect(() => {
    let unsubscribe: () => void = () => undefined;
    void Promise.resolve().then(() => {
      try {
        unsubscribe = onIdTokenChanged(getFirebaseAuth(), (nextUser) => {
          setUser(nextUser);
          setLoading(false);
        });
      } catch (error) {
        setConfigurationError(
          error instanceof Error ? error.message : "Authentication is not configured.",
        );
        setLoading(false);
      }
    });
    return () => unsubscribe();
  }, []);

  const signInWithGoogle = useCallback(async () => {
    await signInWithPopup(getFirebaseAuth(), new GoogleAuthProvider());
  }, []);
  const signInWithEmail = useCallback(async (email: string, password: string) => {
    await signInWithEmailAndPassword(getFirebaseAuth(), email, password);
  }, []);
  const createEmailAccount = useCallback(async (email: string, password: string) => {
    await createUserWithEmailAndPassword(getFirebaseAuth(), email, password);
  }, []);
  const signOut = useCallback(async () => firebaseSignOut(getFirebaseAuth()), []);

  const value = useMemo(
    () => ({
      user,
      loading,
      configurationError,
      signInWithGoogle,
      signInWithEmail,
      createEmailAccount,
      signOut,
    }),
    [
      user,
      loading,
      configurationError,
      signInWithGoogle,
      signInWithEmail,
      createEmailAccount,
      signOut,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
