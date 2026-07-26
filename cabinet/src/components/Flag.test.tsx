import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Flag } from "./Flag";

describe("Flag (локальные флаги вместо flagcdn)", () => {
  it("рендерит span.fi-<код> для валидного кода", () => {
    const { container } = render(<Flag code="ru" />);
    expect(container.querySelector("span")?.className).toContain("fi-ru");
  });

  it("приводит код к нижнему регистру", () => {
    const { container } = render(<Flag code="PL" />);
    expect(container.querySelector("span")?.className).toContain("fi-pl");
  });

  it("для пустого/невалидного кода — глобус, без битого флага", () => {
    const { container } = render(<Flag code="" />);
    expect(container.textContent).toContain("🌐");
    expect(container.querySelector('[class*="fi-"]')).toBeNull();
  });
});
