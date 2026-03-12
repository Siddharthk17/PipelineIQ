import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// Mock the auth context
vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

// Mock the API — keep ApiError real for error type checks
const { ApiError: RealApiError } = await import("@/lib/api");
vi.mock("@/lib/api", async (importOriginal) => {
  const orig = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...orig,
    login: vi.fn(),
    register: vi.fn(),
    setToken: vi.fn(),
  };
});

import { useAuth } from "@/lib/auth-context";
import LoginPage from "@/app/login/page";
import RegisterPage from "@/app/register/page";

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/login",
}));

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isLoading: false,
      login: vi.fn().mockResolvedValue(undefined),
      loginAsDemo: vi.fn().mockResolvedValue(undefined),
      logout: vi.fn(),
    });
  });

  it("renders login form with email and password inputs", () => {
    render(<LoginPage />);
    expect(screen.getByPlaceholderText("you@example.com")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("••••••••")).toBeInTheDocument();
  });

  it("renders sign in button", () => {
    render(<LoginPage />);
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("renders demo login button", () => {
    render(<LoginPage />);
    expect(screen.getByText(/try demo/i)).toBeInTheDocument();
  });

  it("renders link to register page", () => {
    render(<LoginPage />);
    expect(screen.getByText("register")).toBeInTheDocument();
  });

  it("calls login on form submit", async () => {
    const loginFn = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isLoading: false,
      login: loginFn,
      loginAsDemo: vi.fn(),
      logout: vi.fn(),
    });

    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByPlaceholderText("you@example.com"), "test@test.com");
    await user.type(screen.getByPlaceholderText("••••••••"), "password123");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(loginFn).toHaveBeenCalledWith("test@test.com", "password123");
    });
  });

  it("calls loginAsDemo on demo button click", async () => {
    const demoFn = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isLoading: false,
      login: vi.fn(),
      loginAsDemo: demoFn,
      logout: vi.fn(),
    });

    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByText(/try demo/i));

    await waitFor(() => {
      expect(demoFn).toHaveBeenCalled();
    });
  });

  it("displays error message on login failure", async () => {
    const loginFn = vi.fn().mockRejectedValue(new Error("network error"));
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isLoading: false,
      login: loginFn,
      loginAsDemo: vi.fn(),
      logout: vi.fn(),
    });

    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByPlaceholderText("you@example.com"), "bad@test.com");
    await user.type(screen.getByPlaceholderText("••••••••"), "wrong");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    });
  });
});

describe("RegisterPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isLoading: false,
      login: vi.fn(),
      loginAsDemo: vi.fn(),
      logout: vi.fn(),
    });
  });

  it("renders registration form", () => {
    render(<RegisterPage />);
    expect(screen.getByPlaceholderText("you@example.com")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("your_username")).toBeInTheDocument();
    // Two password fields with same placeholder
    const passwordFields = screen.getAllByPlaceholderText("••••••••");
    expect(passwordFields.length).toBeGreaterThanOrEqual(2);
  });

  it("renders create account button", () => {
    render(<RegisterPage />);
    expect(screen.getByRole("button", { name: /create account/i })).toBeInTheDocument();
  });

  it("renders link to login page", () => {
    render(<RegisterPage />);
    expect(screen.getByText("sign in")).toBeInTheDocument();
  });

  it("shows error on password mismatch", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    await user.type(screen.getByPlaceholderText("you@example.com"), "test@test.com");
    await user.type(screen.getByPlaceholderText("your_username"), "tester");

    const passwordFields = screen.getAllByPlaceholderText("••••••••");
    await user.type(passwordFields[0], "password123");
    await user.type(passwordFields[1], "different456");

    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(screen.getByText(/match/i)).toBeInTheDocument();
    });
  });

  it("shows error on short password", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    await user.type(screen.getByPlaceholderText("you@example.com"), "test@test.com");
    await user.type(screen.getByPlaceholderText("your_username"), "tester");

    const passwordFields = screen.getAllByPlaceholderText("••••••••");
    await user.type(passwordFields[0], "short");
    await user.type(passwordFields[1], "short");

    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(screen.getByText(/8 characters/i)).toBeInTheDocument();
    });
  });
});
