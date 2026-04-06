import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { AuthProvider, useAuth } from "@/lib/auth-context";

// Test component that exposes auth context
function AuthConsumer() {
  const { user, isLoading, login, loginAsDemo, logout } = useAuth();
  return (
    <div>
      <p data-testid="loading">{isLoading ? "loading" : "ready"}</p>
      <p data-testid="user">{user ? user.username : "none"}</p>
      <p data-testid="role">{user ? user.role : "none"}</p>
      <p data-testid="demo">{user?.isDemo ? "yes" : "no"}</p>
      <button data-testid="login" onClick={() => login("test@test.com", "pass")}>Login</button>
      <button data-testid="demo-login" onClick={() => loginAsDemo()}>Demo</button>
      <button data-testid="logout" onClick={() => logout()}>Logout</button>
    </div>
  );
}

describe("AuthProvider", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    document.cookie = "piq_auth=; max-age=0";
  });

  it("initially has no user and is loading", async () => {
    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    // After mount with no token, loading becomes false quickly
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("ready"));
    expect(screen.getByTestId("user").textContent).toBe("none");
  });

  it("login sets user on success", async () => {
    const user = userEvent.setup();

    // No token on mount → useEffect skips getMe
    // login() calls apiLogin then getMe — 2 fetches
    vi.spyOn(globalThis, "fetch")
      // apiLogin call
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "tok-123",
            token_type: "bearer",
            expires_in: 3600,
            user: { id: "u1", email: "test@test.com", username: "tester", role: "admin", is_active: true, created_at: "" },
          }),
      } as Response)
      // getMe call after login
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({ id: "u1", email: "test@test.com", username: "tester", role: "admin", is_active: true, created_at: "" }),
      } as Response);

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("ready"));

    await user.click(screen.getByTestId("login"));

    await waitFor(() => {
      expect(screen.getByTestId("user").textContent).toBe("tester");
      expect(screen.getByTestId("role").textContent).toBe("admin");
    });
  });

  it("loginAsDemo uses demo credentials", async () => {
    const user = userEvent.setup();

    vi.spyOn(globalThis, "fetch")
      // apiLogin (demo)
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "demo-tok",
            token_type: "bearer",
            expires_in: 3600,
            user: { id: "demo", email: "demo@pipelineiq.app", username: "demo", role: "viewer", is_active: true, created_at: "" },
          }),
      } as Response)
      // getMe after demo login
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({ id: "demo", email: "demo@pipelineiq.app", username: "demo", role: "viewer", is_active: true, created_at: "" }),
      } as Response);

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("ready"));

    await user.click(screen.getByTestId("demo-login"));

    await waitFor(() => {
      expect(screen.getByTestId("user").textContent).toBe("demo");
      expect(screen.getByTestId("demo").textContent).toBe("yes");
    });
  });

  it("logout clears user and token", async () => {
    const user = userEvent.setup();

    vi.spyOn(globalThis, "fetch")
      // apiLogin
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "tok",
            token_type: "bearer",
            expires_in: 3600,
            user: { id: "u1", email: "t@t.com", username: "tester", role: "admin", is_active: true, created_at: "" },
          }),
      } as Response)
      // getMe after login
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({ id: "u1", email: "t@t.com", username: "tester", role: "admin", is_active: true, created_at: "" }),
      } as Response);

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("ready"));

    await user.click(screen.getByTestId("login"));
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("tester"));

    await user.click(screen.getByTestId("logout"));
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("none"));
    expect(localStorage.getItem("pipelineiq_token")).toBeNull();
  });
});
