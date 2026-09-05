import { access, copyFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

if (process.platform !== "win32") {
  process.exit(0);
}

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const vendorDir = path.join(projectRoot, "node_modules", "electron-winstaller", "vendor");
const architecture = process.arch === "arm64" ? "arm64" : "x64";

for (const extension of ["exe", "dll"]) {
  const source = path.join(vendorDir, `7z-${architecture}.${extension}`);
  const target = path.join(vendorDir, `7z.${extension}`);
  await access(source);
  await copyFile(source, target);
}

console.log(`Prepared Squirrel 7-Zip helper for ${architecture}.`);
