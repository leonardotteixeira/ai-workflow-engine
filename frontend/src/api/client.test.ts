import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";

afterEach(() => vi.restoreAllMocks());

describe("api client error handling", () => {
  it("throws ApiError with the server's detail message on a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "no execution with id 'x'" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.getExecution("x")).rejects.toMatchObject({
      message: "no execution with id 'x'",
      status: 404,
    });
  });

  it("falls back to a generic message when the error body isn't JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("oops", { status: 500 })));

    await expect(api.getExecution("x")).rejects.toBeInstanceOf(ApiError);
  });

  it("never leaks a raw network exception message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("internal DNS resolver failure at 10.0.0.5")),
    );

    try {
      await api.getExecution("x");
      throw new Error("expected rejection");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).message).not.toContain("10.0.0.5");
      expect((e as ApiError).message).toMatch(/could not reach the server/i);
    }
  });

  it("resolves with parsed JSON on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(api.health()).resolves.toEqual({ status: "ok" });
  });
});
