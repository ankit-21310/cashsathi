import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  RulesTestEnvironment,
  assertFails,
  initializeTestEnvironment,
} from "@firebase/rules-unit-testing";
import { doc, getDoc, setDoc } from "firebase/firestore";
import { readFile } from "node:fs/promises";

describe("Firestore client boundary", () => {
  let environment: RulesTestEnvironment;

  beforeAll(async () => {
    environment = await initializeTestEnvironment({
      projectId: "cashsathi-local",
      firestore: {
        host: "127.0.0.1",
        port: 8080,
        rules: await readFile("infra/firebase/firestore.rules", "utf8"),
      },
    });
  });

  afterAll(async () => {
    await environment.cleanup();
  });

  it("denies unauthenticated reads", async () => {
    const db = environment.unauthenticatedContext().firestore();
    await assertFails(getDoc(doc(db, "businesses", "biz_test")));
  });

  it("denies authenticated reads and writes", async () => {
    const db = environment.authenticatedContext("alice").firestore();
    const business = doc(db, "businesses", "biz_test");
    await assertFails(getDoc(business));
    await assertFails(setDoc(business, { name: "Unsafe client write" }));
  });
});
