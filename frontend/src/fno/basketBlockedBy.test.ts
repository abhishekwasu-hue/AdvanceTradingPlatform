import { describe, expect, it } from "vitest";
import basketSource from "./BasketPanelV2.tsx?raw";
import { basketBlockedBy } from "./ticketModel";

/** Owner bug 29 Oct: the basket confirm said "Cannot place" with its reason below the screen on a phone. */
describe("basket: why it cannot be placed, beside the button", () => {
  const leg = (refusals: string[] = [], errors: string[] = []) => ({ errors, refusals });

  it("nothing when the basket can be placed", () => {
    expect(basketBlockedBy({ can_execute: true, refusals: [], legs: [leg()] })).toBeNull();
  });

  it("a leg's own-limit refusal comes first, with the link to My limits", () => {
    const why = basketBlockedBy({ can_execute: false, refusals: ["basket room"],
      legs: [leg(["One lot risks more than your risk per trade allows. Set a closer stop-loss or a smaller position."])] });
    expect(why?.text).toMatch(/^One lot risks more than your risk per trade allows/);
    expect(why?.link).toBe("/risk-management");
  });

  it("an error beats a refusal; the basket's own reason when no leg has one", () => {
    expect(basketBlockedBy({ can_execute: false, refusals: [], legs: [leg(["r"], ["No live price. Nothing can be placed without it."])] })?.text)
      .toBe("No live price. Nothing can be placed without it.");
    expect(basketBlockedBy({ can_execute: false, refusals: ["This basket opens 3 positions"], legs: [leg()] })?.text)
      .toBe("This basket opens 3 positions");
  });

  it("rendered in the footer, above the Place button", () => {
    expect(basketSource.indexOf('data-basket="blocked-by"')).toBeLessThan(basketSource.indexOf('data-basket="place"'));
  });
});
