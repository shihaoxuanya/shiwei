import { build } from "esbuild";
import { mkdir } from "node:fs/promises";

await mkdir(".electron", { recursive: true });
const shared = { bundle: true, platform: "node", format: "esm", target: "node22", sourcemap: true, packages: "external", external: ["electron"] };
await build({ ...shared, entryPoints: ["electron/main.ts"], outfile: ".electron/main.mjs" });
await build({ ...shared, entryPoints: ["electron/preload.ts"], outfile: ".electron/preload.mjs" });
