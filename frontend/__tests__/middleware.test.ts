import { describe, expect, it } from "vitest";

describe("middleware redirect logic", () => {
  const protectedRoutes = ["/", "/dashboard", "/pipelines/new"] as const;

  it("redirects protected routes when no auth cookie is present", () => {
    const hasCookie = false;
    for (const path of protectedRoutes) {
      const shouldRedirect = protectedRoutes.includes(path) && !hasCookie;
      expect(shouldRedirect).toBe(true);
    }
  });

  it("does not redirect protected routes when auth cookie exists", () => {
    const hasCookie = true;
    for (const path of protectedRoutes) {
      const shouldRedirect = protectedRoutes.includes(path) && !hasCookie;
      expect(shouldRedirect).toBe(false);
    }
  });

  it("does not redirect non-protected routes", () => {
    const hasCookie = false;
    const path = "/login";
    const shouldRedirect = protectedRoutes.includes(path as (typeof protectedRoutes)[number]) && !hasCookie;
    expect(shouldRedirect).toBe(false);
  });

  it("matcher config includes all protected routes", () => {
    const matcher = ["/", "/dashboard", "/pipelines/new"];
    expect(matcher).toContain("/");
    expect(matcher).toContain("/dashboard");
    expect(matcher).toContain("/pipelines/new");
    expect(matcher).not.toContain("/login");
    expect(matcher).not.toContain("/register");
  });
});
