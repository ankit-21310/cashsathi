import { expect, test, type Page } from "@playwright/test";

const publicViewports = [
  { name: "wide desktop", width: 1920, height: 1080 },
  { name: "tablet", width: 1024, height: 768 },
  { name: "phone", width: 390, height: 844 },
];

async function expectNoHorizontalOverflow(page: Page) {
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true);
}

async function signInAsOwner(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("owner-a@example.test");
  await page.getByLabel("Password").fill("DemoPass!123");
  await page.locator("form").getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

for (const viewport of publicViewports) {
  test(`public pages fit the ${viewport.name} viewport`, async ({ page }) => {
    await page.setViewportSize(viewport);

    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page);

    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

test("wide landing sections share the same outer alignment", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");

  const edges = await page.locator(".landing-nav, .hero, .workflow-grid, .safety-panel, .pricing-panel").evaluateAll(
    (elements) => elements.map((element) => Math.round(element.getBoundingClientRect().left)),
  );
  expect(new Set(edges).size).toBe(1);
});

test("authenticated routes remain reachable without horizontal overflow", async ({ page }) => {
  test.skip(!process.env.E2E_FIREBASE, "Requires running Auth, Firestore, and API emulators");
  test.setTimeout(90_000);

  await page.setViewportSize({ width: 1024, height: 768 });
  await signInAsOwner(page);

  const routes = [
    "/dashboard",
    "/invoices/new",
    "/activity",
    "/approvals",
    "/impact",
    "/integrations/gmail",
    "/settings",
    "/team",
    "/finance",
    "/privacy",
    "/admin/impact",
    "/admin/validation",
  ];

  for (const route of routes) {
    await page.goto(route);
    await expect(page.locator("main")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard");
  await expect(page.locator(".data-pill")).toBeVisible();
  const menuButton = page.getByRole("button", { name: "Menu" });
  await expect(menuButton).toBeVisible();
  await menuButton.click();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Privacy" })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.keyboard.press("Escape");
  await expect(menuButton).toBeFocused();
  await expect(menuButton).toHaveAttribute("aria-expanded", "false");
});
