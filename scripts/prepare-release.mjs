import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { root, parseVersion, validateVersion } from './version.mjs';

const config = JSON.parse(readFileSync(resolve(root, 'release.config.json'), 'utf8'));
const version = validateVersion();
if (parseVersion(version).prerelease || config.channel !== 'stable') throw new Error('Only stable releases are enabled');
const pubkey = process.env.SHIWEI_UPDATER_PUBLIC_KEY || config.updaterPublicKey;
if (!pubkey || !Buffer.from(pubkey, 'base64').toString().startsWith('untrusted comment:')) throw new Error('Official Tauri updater public key is required');
if (!process.env.TAURI_SIGNING_PRIVATE_KEY) throw new Error('TAURI_SIGNING_PRIVATE_KEY is required; refusing unsigned release');
mkdirSync(resolve(root, 'output/release'), { recursive: true });
writeFileSync(resolve(root, 'output/release/tauri.release.json'), JSON.stringify({
  bundle: { createUpdaterArtifacts: true, ...(process.env.SHIWEI_WINDOWS_SIGN_COMMAND ? { windows: { signCommand: process.env.SHIWEI_WINDOWS_SIGN_COMMAND } } : {}) },
  plugins: { updater: { pubkey, endpoints: [], windows: { installMode: 'passive' } } },
}, null, 2));
console.log('Validated public release configuration; private keys are never written to config.');
