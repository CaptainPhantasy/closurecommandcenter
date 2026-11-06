import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

describe("commercial product identity", () => {
  it("ships a transparent RGBA icon pyramid and browser metadata", () => {
    for (const [name, size] of [["favicon-16x16.png",16],["favicon-32x32.png",32],["apple-touch-icon.png",180],["icon-192.png",192],["icon-512.png",512]] as const) {
      const data = readFileSync(resolve("public", name));
      expect(data.readUInt32BE(16)).toBe(size);
      expect(data.readUInt32BE(20)).toBe(size);
      expect(data[25]).toBe(6);
    }
    expect(JSON.parse(readFileSync("public/manifest.webmanifest", "utf8")).icons.length).toBeGreaterThanOrEqual(2);
    const html = readFileSync("index.html", "utf8");
    for (const rel of ['rel="icon"', 'rel="apple-touch-icon"', 'rel="manifest"']) expect(html).toContain(rel);
    expect(readFileSync("public/favicon.svg", "utf8")).not.toContain("<polygon");
  });
});

// Product chrome must not depend on platform-specific emoji glyphs.
it("uses no pictographic emoji in shipped TSX", () => {
  const walk = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(`${dir}/${e.name}`) : [`${dir}/${e.name}`]);
  for (const file of walk("src").filter(f => f.endsWith(".tsx") && !f.includes(".test."))) {
    expect(readFileSync(file, "utf8"), file).not.toMatch(/\p{Extended_Pictographic}/u);
  }
});
