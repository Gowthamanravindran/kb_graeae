import "@testing-library/jest-dom/vitest";

// jsdom lacks these; React Flow and Radix need them.
class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as any).ResizeObserver ??= RO;
(globalThis as any).DOMMatrixReadOnly ??= class { m22 = 1; constructor() {} };
Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get() { return 100; } });
Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get() { return 200; } });
(globalThis as any).scrollTo ??= () => {};
Element.prototype.scrollIntoView ??= function () {};
