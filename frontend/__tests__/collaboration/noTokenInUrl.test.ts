import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

// F-4: the yjs relay URL must never carry the JWT access token. The browser
// sends the HttpOnly cookie on the handshake and the relay verifies it as a
// fallback (y-websocket/server.js: tokenFromCookie), so the token param is
// pure credential exposure (proxy/access logs).
vi.mock("y-websocket", () => ({
  // NOTE: regular function, not an arrow — the hook constructs this with
  // `new`, and arrows are not constructors.
  WebsocketProvider: vi.fn(function (this: unknown) {
    return {
      on: vi.fn(),
      off: vi.fn(),
      disconnect: vi.fn(),
      destroy: vi.fn(),
      awareness: null,
    };
  }),
}));

// eslint-disable-next-line import/first
import { WebsocketProvider } from "y-websocket";
// eslint-disable-next-line import/first
import { useCollaborativePipeline } from "@/hooks/useCollaborativePipeline";

const SECRET = "test-jwt-f4c9-redacted";

function renderWithToken(authToken?: string) {
  return renderHook(() =>
    useCollaborativePipeline({
      pipelineName: "room-f4",
      initialNodes: [],
      initialEdges: [],
      initialYaml: "",
      currentUser: { id: "u1", name: "Ada Lovelace" },
      authToken,
      onNodesChange: () => {},
      onEdgesChange: () => {},
      onYamlChange: () => {},
      onNodeConfigure: () => {},
    }),
  );
}

// Mirror of y-websocket's `get url()` serialization: room path plus the
// captured params object as the query string.
function constructedUrl(params: Record<string, unknown>): string {
  const query = new URLSearchParams(
    params as Record<string, string>,
  ).toString();
  return `ws://localhost:1234/room-f4${query ? `?${query}` : ""}`;
}

describe("F-4 no JWT in yjs WebSocket URL", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("never passes the access token as a provider param", () => {
    const { unmount } = renderWithToken(SECRET);
    try {
      const ctor = vi.mocked(WebsocketProvider);
      expect(ctor).toHaveBeenCalled();
      const options = ctor.mock.calls[0][3] as {
        params?: Record<string, unknown>;
      };
      const params = options?.params ?? {};
      expect(params).not.toHaveProperty("token");
      for (const value of Object.values(params)) {
        expect(String(value)).not.toContain(SECRET);
      }
      const url = constructedUrl(params);
      expect(url).not.toContain("token=");
      expect(url).not.toContain(SECRET);
    } finally {
      unmount();
    }
  });

  it("still connects via the HttpOnly cookie when no token is in state", () => {
    // Post-reload sessions carry auth only as the HttpOnly cookie.
    const { unmount } = renderWithToken(undefined);
    try {
      const ctor = vi.mocked(WebsocketProvider);
      expect(ctor).toHaveBeenCalled();
      const options = ctor.mock.calls[0][3] as {
        params?: Record<string, unknown>;
      };
      expect(options?.params ?? {}).not.toHaveProperty("token");
    } finally {
      unmount();
    }
  });
});
