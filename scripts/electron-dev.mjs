import { spawn } from "node:child_process";
import { build } from "esbuild";
import waitOn from "wait-on";

const shell = process.platform === "win32";
const vite = spawn("pnpm", ["dev", "--", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], { stdio: "inherit", shell });
await waitOn({ resources: ["http://127.0.0.1:4173"], timeout: 60_000 });
await build({ bundle: true, platform: "node", format: "esm", target: "node22", sourcemap: true, packages: "external", external: ["electron"], entryPoints: ["electron/main.ts"], outfile: ".electron/main.mjs" });
await build({ bundle: true, platform: "node", format: "esm", target: "node22", sourcemap: true, packages: "external", external: ["electron"], entryPoints: ["electron/preload.ts"], outfile: ".electron/preload.mjs" });
const electron = spawn("pnpm", ["exec", "electron", "."], { stdio: "inherit", shell, env: { ...process.env, BRAIN_DEV_URL: "http://127.0.0.1:4173" } });
const stop = () => { vite.kill(); electron.kill(); };
electron.on("exit", (code) => { stop(); process.exit(code ?? 0); });
process.on("SIGINT", stop);
