import { expect, test } from "@playwright/test";

const BLANK_PDF_BASE64 =
  "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgMjAwIDIwMCBdCi9QYXJlbnQgMiAwIFIKPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDE2MiAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDUKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKPj4Kc3RhcnR4cmVmCjI1NgolJUVPRgo=";

test.describe("fresh browser owner journey", () => {
  test.skip(!process.env.E2E_FIREBASE, "Requires running Auth, Firestore, and API emulators");

  test("signs in, reaches the same tenant, signs out, and signs in again", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("owner-a@example.test");
    await page.getByLabel("Password").fill("DemoPass!123");
    await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole("heading", { name: "Aster Studio" })).toBeVisible();

    await page.getByRole("button", { name: /Account menu for/ }).click();
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.getByLabel("Email").fill("owner-a@example.test");
    await page.getByLabel("Password").fill("DemoPass!123");
    await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole("heading", { name: "Aster Studio" })).toBeVisible();
  });

  test("runs the controlled reminder and verified-payment journey", async ({ page }) => {
    const unique = Date.now();
    await page.goto("/login");
    await page.getByRole("button", { name: "Create account" }).click();
    await page.getByLabel("Email").fill(`phase45-${unique}@example.test`);
    await page.getByLabel("Password").fill("DemoPass!123");
    await page.locator("form").getByRole("button", { name: "Create account" }).click();

    await expect(page).toHaveURL(/\/onboarding$/);
    await page.getByLabel("Business name").fill(`Phase 45 Studio ${unique}`);
    await page.getByRole("button", { name: "Create business workspace" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByText("Unclassified data", { exact: false }).first()).toBeVisible();

    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("link", { name: "Gmail integration", exact: true }).click();
    await page.getByRole("button", { name: "Connect Gmail" }).click();
    await expect(page).toHaveURL(/\/integrations\/gmail\?status=connected$/);
    await expect(page.getByText("Gmail connected", { exact: true })).toBeVisible();
    await expect(page.getByText("Approval-only mode", { exact: true })).toBeVisible();

    await page.getByRole("link", { name: "Upload", exact: true }).click();
    await page.getByLabel(/I have read and accept/).check();
    await page.getByRole("button", { name: "Accept and continue" }).click();
    await page.getByLabel("Invoice PDF").setInputFiles({
      name: `phase45-${unique}.pdf`,
      mimeType: "application/pdf",
      buffer: Buffer.from(BLANK_PDF_BASE64, "base64"),
    });
    await page.getByRole("button", { name: "Extract invoice facts" }).click();
    await expect(page.getByRole("heading", { name: "Confirm every extracted fact" })).toBeVisible();
    await page.getByLabel("Invoice number").fill(`E2E-${unique}`);
    await page.getByLabel(/I checked these fields/).check();
    await page.getByRole("button", { name: "Confirm invoice" }).click();

    await expect(page).toHaveURL(/\/invoices\/inv_/);
    await page.getByRole("button", { name: "Evaluate invoice" }).click();
    await expect(page.getByRole("link", { name: "Review action" })).toBeVisible();
    await page.getByRole("button", { name: "Evaluate again" }).click();
    await page.getByRole("link", { name: "Review action" }).click();
    await expect(page.getByText("Awaiting approval", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve & send" })).toHaveCount(1);
    await page.getByRole("button", { name: "Approve & send" }).click();
    await expect(page.getByText("Succeeded", { exact: true })).toBeVisible();

    await page.getByRole("link", { name: "View invoice" }).click();
    await expect(page.getByText("Reminder succeeded", { exact: true })).toBeVisible();
    await page.getByLabel("Amount").fill("50000.00");
    const paidAt = new Date(Date.now() - 60_000).toISOString().slice(0, 16);
    await page.getByLabel("Paid at").fill(paidAt);
    await page.getByLabel("Bank / UPI reference").fill(`BANK-${unique}`);
    await page.getByLabel(/I confirm this payment was received/).check();
    await page.getByRole("button", { name: "Record verified payment" }).click();

    await expect(page.getByText("Paid", { exact: true })).toBeVisible();
    await expect(page.getByText("Verified payment recorded", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Evaluate again" })).toBeDisabled();
  });

  test("opens the admin validation workspace and downloads sanitized evidence", async ({ page }) => {
    const unique = Date.now();
    await page.goto("/login");
    await page.getByLabel("Email").fill("owner-a@example.test");
    await page.getByLabel("Password").fill("DemoPass!123");
    await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("link", { name: "Admin", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Platform impact" })).toBeVisible();
    await page.getByRole("link", { name: "Validation workspace" }).click();
    await page.getByLabel("Company").fill(`Public Demo Company ${unique}`);
    await page.getByLabel("Segment").fill("Agency");
    await page.getByLabel("Public website").fill("https://example.test");
    await page.getByLabel("Public contact channel").fill("Public website contact form");
    await page.getByRole("button", { name: "Add prospect" }).click();
    await expect(
      page.locator(".validation-row").filter({ hasText: `Public Demo Company ${unique}` }),
    ).toBeVisible();

    await page.getByRole("link", { name: "Evidence dashboard" }).click();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download evidence ZIP" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("cashsathi-evidence.zip");
  });

  test("keeps privacy consent content inside responsive cards", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("owner-a@example.test");
    await page.getByLabel("Password").fill("DemoPass!123");
    await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.goto("/privacy");

    const cards = page.locator(".optional-consent-card");
    await expect(cards).toHaveCount(3);
    for (let index = 0; index < 3; index += 1) {
      const cardBox = await cards.nth(index).boundingBox();
      const labelBox = await cards.nth(index).locator(".eyebrow").boundingBox();
      expect(cardBox).not.toBeNull();
      expect(labelBox).not.toBeNull();
      expect(labelBox!.x - cardBox!.x).toBeGreaterThanOrEqual(20);
    }

    await page.setViewportSize({ width: 600, height: 900 });
    await expect(cards.first()).toBeVisible();
    const fitsViewport = await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    );
    expect(fitsViewport).toBe(true);
  });
});
