import { createHash } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { root, validateVersion } from './version.mjs';
import { artifactBaseUrl, updateMetadata } from './release-artifacts.mjs';

export async function notifyReady(origin, token, payload, request = fetch) {
  const url = new URL(origin);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('A credential-free HTTPS control plane origin is required');
  if (!token || /[\r\n]/.test(token)) throw new Error('Missing valid release publish credential');
  // Never follow redirects carrying CI credentials or log response bodies.
  const response = await request(new URL('/internal/releases/artifact-ready', url), {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(900_000),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Artifact registration failed (HTTP ${response.status}); no rollout was requested`);
  const data = await response.json();
  if (data.version !== payload.version || data.status !== 'READY') throw new Error('Control plane did not confirm READY; inspect release state without republishing');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const config = JSON.parse(readFileSync(resolve(root, 'release.config.json'), 'utf8'));
    const version = validateVersion(root, process.env.RELEASE_TAG);
    const directory = resolve(root, 'output/release/artifacts');
    const filename = `Shiwei_${version}_x64-setup.exe`;
    const path = resolve(directory, filename);
    const signature = readFileSync(path + '.sig', 'utf8').trim();
    const notes = readFileSync(resolve(directory, 'release-notes.md'), 'utf8').trim();
    const artifact_url = `${artifactBaseUrl(config, process.env, version)}/${filename}`;
    updateMetadata(version, artifact_url, signature, notes);
    await notifyReady(config.controlPlaneOrigin, process.env.SHIWEI_RELEASE_PUBLISH_TOKEN, {
      version, channel: 'stable', artifact_url, signature,
      sha256: createHash('sha256').update(readFileSync(path)).digest('hex'),
      size: statSync(path).size, release_notes: notes,
    });
    console.log('Control plane confirmed READY. Administrator approval is required for rollout.');
  } catch {
    console.error('READY registration failed; check control plane availability, credentials and artifact verification. No rollout was requested.');
    process.exitCode = 1;
  }
}
