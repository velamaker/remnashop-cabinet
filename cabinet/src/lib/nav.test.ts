import { describe, it, expect } from "vitest";
import { safeInternalPath } from "./nav";

describe("safeInternalPath — защита от open-redirect", () => {
  it("пропускает внутренние пути", () => {
    expect(safeInternalPath("/devices")).toBe("/devices");
    expect(safeInternalPath("/billing?x=1")).toBe("/billing?x=1");
    expect(safeInternalPath("/")).toBe("/");
  });

  it("режет внешние и protocol-relative", () => {
    expect(safeInternalPath("//evil.com")).toBe("/");
    expect(safeInternalPath("http://evil.com")).toBe("/");
    expect(safeInternalPath("https://evil.com")).toBe("/");
  });

  it("режет обход через обратный слэш (CVE react-router)", () => {
    expect(safeInternalPath("/\\evil.com")).toBe("/");
    expect(safeInternalPath("\\\\evil.com")).toBe("/");
    expect(safeInternalPath("/path\\..\\x")).toBe("/");
  });

  it("фолбэк на / при пустом", () => {
    expect(safeInternalPath(null)).toBe("/");
    expect(safeInternalPath(undefined)).toBe("/");
    expect(safeInternalPath("")).toBe("/");
  });
});
