import { expect, test } from "@playwright/test";

test.describe("production judge smoke", () => {
  test.skip(
    !process.env.JUDGE_EMAIL || !process.env.JUDGE_PASSWORD || !process.env.E2E_BASE_URL,
    "Requires a provisioned judge account and production base URL",
  );

  test("opens a clean browser, signs in, and verifies safe judge defaults", async ({ page }) => {
    const landing = await page.goto("/");
    expect(landing?.headers()["x-content-type-options"]).toBe("nosniff");
    expect(landing?.headers()["content-security-policy"]).toContain("default-src 'self'");

    await page.goto("/login");
    await page.getByLabel("Email").fill(process.env.JUDGE_EMAIL!);
    await page.getByLabel("Password").fill(process.env.JUDGE_PASSWORD!);
    await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByText("Demo data", { exact: true })).toBeVisible();
    await expect(page.getByText("Off", { exact: true })).toBeVisible();

    await page.getByRole("link", { name: "Privacy", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Privacy and plan" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Download my data" })).toBeVisible();
  });
});
