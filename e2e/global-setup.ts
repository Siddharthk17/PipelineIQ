import { FullConfig, request } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Global setup: authenticate the shared demo user ONCE via the API and
 * persist the session (HttpOnly cookie) to storageState. Feature specs
 * (02-12) reuse it through `test.use({ storageState })`, so the suite
 * does not hammer the auth rate limiter (5/min) with a UI login per test.
 */
async function globalSetup(_config: FullConfig) {
  const apiUrl = process.env.E2E_API_URL || "http://127.0.0.1:8000";
  const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
  const password = process.env.E2E_PASSWORD ?? "Demo1234!";

  const authFile = path.join(__dirname, ".auth", "demo.json");
  fs.mkdirSync(path.dirname(authFile), { recursive: true });

  const ctx = await request.newContext();
  let ok = false;
  for (let attempt = 0; ; attempt++) {
    const resp = await ctx.post(`${apiUrl}/auth/login`, {
      data: { email, password },
    });
    if (resp.ok()) {
      ok = true;
      break;
    }
    if (resp.status() !== 429 || attempt >= 11) {
      throw new Error(`global-setup login failed: ${resp.status()} ${await resp.text()}`);
    }
    await new Promise((r) => setTimeout(r, 10_000));
  }
  if (!ok) throw new Error("global-setup login failed");
  await ctx.storageState({ path: authFile });
  await ctx.dispose();
  // The API lives on 127.0.0.1 but tests browse http://localhost: the
  // cookie jar must cover both hosts or the session won't apply.
  const state = JSON.parse(fs.readFileSync(authFile, "utf-8")) as {
    cookies: { domain?: string; name: string }[];
  };
  for (const cookie of state.cookies) {
    if (cookie.domain === "127.0.0.1") {
      state.cookies.push({ ...cookie, domain: "localhost" });
    }
  }
  fs.writeFileSync(authFile, JSON.stringify(state));
}

export default globalSetup;
