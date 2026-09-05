import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { root, validateVersion, parseVersion } from './version.mjs';
export function artifactBaseUrl(config, env, version) {
  const parsed = parseVersion(version);
  if (parsed.prerelease || version.includes('+')) throw new Error('Stable artifact version required');
  // GITHUB_REPOSITORY is the private source repository in a split-repository build.
  // It must never silently become the public installer download location.
  const repository = env.SHIWEI_RELEASE_REPOSITORY || config.artifactRepository;
  if (!repository || !/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(repository)) throw new Error('Configure a public artifact repository');
  if (repository.toLowerCase() === config.sourceRepository?.toLowerCase()) throw new Error('Artifacts must not point to the private source repository');
  return `https://github.com/${repository}/releases/download/v${version}`;
}
export function releaseNotes(changelog, version) {
  const heading = `## ${version}\n`;
  const normalized = changelog.replace(/\r\n/g, '\n');
  const start = normalized.indexOf(heading);
  if (start < 0) throw new Error('Missing curated release notes');
  const body = normalized.slice(start + heading.length).split(/\n## /)[0].trim();
  if (!body || body.length > 8000) throw new Error('Release notes must contain 1–8000 characters');
  return body;
}
export function updateMetadata(version, url, signature, notes) {
  if (parseVersion(version).prerelease) throw new Error('Stable metadata cannot point to a prerelease');
  let parsed;
  try { parsed = new URL(url); }
  catch { throw new Error('Invalid artifact URL'); }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error('HTTPS artifacts only');
  // Signature is the original Tauri .sig contents, not a hash or a URL.
  if (!Buffer.from(signature.trim(), 'base64').toString().startsWith('untrusted comment:')) throw new Error('Missing Tauri signature');
  return { version, notes, pub_date: new Date().toISOString(), platforms: { 'windows-x86_64': { url, signature: signature.trim() } } };
}
export function validateArtifactDirectory(names, version) {
  const filename = `Shiwei_${version}_x64-setup.exe`;
  const allowed = new Set([filename, filename + '.sig', 'latest.json', 'SHA256SUMS.txt', 'release-notes.md']);
  if (names.some((name) => !allowed.has(name))) throw new Error('Artifact directory contains stale or unexpected files; use a clean release staging directory');
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const version = validateVersion(root, process.env.RELEASE_TAG);
  const filename = `Shiwei_${version}_x64-setup.exe`;
  const source = resolve(root, `apps/desktop/src-tauri/target/release/bundle/nsis/拾微_${version}_x64-setup.exe`);
  const output = resolve(root, 'output/release/artifacts');
  const config = JSON.parse(readFileSync(resolve(root, 'release.config.json'), 'utf8'));
  const base = artifactBaseUrl(config, process.env, version);
  const notes = releaseNotes(readFileSync(resolve(root, 'CHANGELOG.md'), 'utf8'), version);
  const metadata = updateMetadata(version, `${base.replace(/\/$/, '')}/${filename}`, readFileSync(source + '.sig', 'utf8'), notes);
  const data = readFileSync(source);
  if (data.length < 1024 || data.subarray(0, 2).toString() !== 'MZ') throw new Error('Missing or invalid Windows installer');
  mkdirSync(output, { recursive: true });
  validateArtifactDirectory(readdirSync(output), version);
  copyFileSync(source, resolve(output, filename)); copyFileSync(source + '.sig', resolve(output, filename + '.sig'));
  writeFileSync(resolve(output, 'latest.json'), JSON.stringify(metadata, null, 2) + '\n');
  writeFileSync(resolve(output, 'release-notes.md'), notes + '\n');
  writeFileSync(resolve(output, 'SHA256SUMS.txt'), `${createHash('sha256').update(data).digest('hex')}  ${filename}\n`);
  console.log(`Prepared complete signed artifact set for ${version}`);
}
