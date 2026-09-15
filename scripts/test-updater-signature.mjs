import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join, sep, dirname } from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { root } from './version.mjs';

const temp = mkdtempSync(join(tmpdir(), 'shiwei-signature-qa-'));
// Use the installed official CLI directly; shell quoting cannot expose or corrupt keys.
const require = createRequire(resolve(root, 'apps/desktop/package.json'));
const cliPackage = require.resolve('@tauri-apps/cli/package.json');
const cli = resolve(dirname(cliPackage), require(cliPackage).bin.tauri);
function tauri(args) {
  const result = spawnSync(process.execPath, [cli, ...args], { cwd: root, encoding: 'utf8', windowsHide: true });
  if (result.status !== 0) throw new Error('Official Tauri signer failed (output suppressed to protect test key)');
}
try {
  const key = join(temp, 'ephemeral.key');
  tauri(['signer', 'generate', '--ci', '--password', '', '--write-keys', key]);
  const artifact = join(temp, 'synthetic-update.bin');
  writeFileSync(artifact, 'Shiwei synthetic signed update; no user data');
  tauri(['signer', 'sign', '--private-key-path', key, '--password', '', artifact]);
  const config = join(temp, 'public-config.json');
  writeFileSync(config, JSON.stringify({ plugins: { updater: { pubkey: readFileSync(key + '.pub', 'utf8').trim() } } }));
  const verifier = resolve(root, 'apps/desktop/src-tauri/target/debug/examples/verify-update.exe');
  if (!existsSync(verifier)) throw new Error('Build the verify-update example first');
  const verify = () => spawnSync(verifier, [artifact, artifact + '.sig', config], { encoding: 'utf8', windowsHide: true }).status;
  if (verify() !== 0) throw new Error('Correct signature was rejected');
  writeFileSync(artifact, 'Tampered update');
  if (verify() === 0) throw new Error('Tampered update was accepted');
  console.log('Official Tauri signing round trip passed; tampered artifact rejected. Ephemeral keys are discarded.');
} finally {
  const parent = resolve(tmpdir()) + sep;
  if (!resolve(temp).startsWith(parent) || !temp.includes('shiwei-signature-qa-')) throw new Error('Unsafe test cleanup path');
  rmSync(temp, { recursive: true, force: true });
}
