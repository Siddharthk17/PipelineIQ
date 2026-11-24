import { describe, it, expect, vi, afterEach } from "vitest";
import { ApiError, checkHealth } from "@/lib/api";

// F-5: checkHealth must throw ApiError (not TypeError) when the error body
// is non-JSON (e.g. a proxy 502 text/html page). The mock below emulates a
// single-use body stream like a real Response: whichever of json()/text()
// runs second rejects with "Body has already been read".
function singleUseErrorBody(html: string) {
  let used = false;
  const markUsed = () => {
    if (used) {
      throw new TypeError("Body is unusable: Body has already been read");
    }
    used = true;
  };
  return {
    ok: false,
    status: 502,
    statusText: "Bad Gateway",
    json: () => {
      markUsed();
      return Promise.reject(
        new SyntaxError("Unexpected token '<', \"<html>...\" is not valid JSON"),
      );
    },
    text: () => {
      markUsed();
      return Promise.resolve(html);
    },
  };
}

describe("F-5 checkHealth with non-JSON error body", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("throws ApiError with the proxy status on text/html 502", async () => {
    vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        singleUseErrorBody("<html>502 Bad Gateway</html>") as unknown as Response,
      );

    const err = await checkHealth().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
  });
});
