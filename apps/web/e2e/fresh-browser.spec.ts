import { expect, test } from "@playwright/test";

test.describe("fresh browser owner journey", () => {
  test.skip(!process.env.E2E_FIREBASE, "Requires running Auth, Firestore, and API emulators");

  test("signs in, reaches the same tenant, signs out, and signs in again", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("owner-a@example.test");
    await page.getByLabel("Password").fill("DemoPass!123");
    await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole("heading", { name: "Aster Studio" })).toBeVisible();
    const tenantNote = page.locator(".tenant-note");
    const firstTenant = await tenantNote.textContent();

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.getByLabel("Email").fill("owner-a@example.test");
    await page.getByLabel("Password").fill("DemoPass!123");
    await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(tenantNote).toContainText(firstTenant?.match(/biz_[a-f0-9]+/)?.[0] ?? "biz_");
  });
});
