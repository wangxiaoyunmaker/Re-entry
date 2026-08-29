import { describe, expect, it } from "vitest";
import { followUpMessage } from "../../web/src/api/mcp.js";

describe("direct delegation resend", () => {
  it("builds a user follow-up with the prompt text verbatim", () => {
    const prompt = "还是不对，再改一下\n保留这段标点。";
    expect(followUpMessage(prompt)).toEqual({
      role: "user",
      content: [{ type: "text", text: prompt }],
    });
  });
});
