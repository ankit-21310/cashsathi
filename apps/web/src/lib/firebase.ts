"use client";

import { getApp, getApps, initializeApp } from "firebase/app";
import { Auth, connectAuthEmulator, getAuth } from "firebase/auth";

import { getPublicEnvironment } from "@/lib/env";

let emulatorConnected = false;

export function getFirebaseAuth(): Auth {
  const environment = getPublicEnvironment();
  const app = getApps().length ? getApp() : initializeApp(environment.firebase);
  const auth = getAuth(app);

  if (environment.useFirebaseEmulator && !emulatorConnected) {
    if (
      typeof window === "undefined" ||
      !["localhost", "127.0.0.1"].includes(window.location.hostname)
    ) {
      throw new Error("Firebase Auth emulator is restricted to local development.");
    }
    connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
    emulatorConnected = true;
  }

  return auth;
}
