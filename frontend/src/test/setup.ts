import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver, but React Flow requires one to measure its
// container — a minimal no-op stand-in is enough for rendering in tests.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
