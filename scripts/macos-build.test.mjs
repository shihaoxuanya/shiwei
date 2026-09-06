import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { artifactNames, contained, requireMac, root } from './macos-build.mjs';

test('Mac builds reject Windows, Intel, Rosetta, and older systems', () => {
  assert.equal(requireMac('darwin', 'arm64', '14.0'), '14.0');
  assert.equal(requireMac('darwin', 'arm64', '26.0'), '26.0');
  for (const input of [['win32', 'x64', '14.0'], ['darwin', 'x64', '14.0'], ['darwin', 'arm64', '13.7'], ['darwin', 'arm64', 'bad']]) {
    assert.throws(() => requireMac(...input));
  }
});

test('Mac overrides do not modify Windows sidecars, installer, or updater', () => {
  const windows = JSON.parse(readFileSync(join(root, 'apps/desktop/src-tauri/tauri.conf.json')));
  const mac = JSON.parse(readFileSync(join(root, 'apps/desktop/src-tauri/tauri.macos.conf.json')));
  assert.deepEqual(windows.bundle.targets, ['nsis']);
  assert.deepEqual(windows.bundle.externalBin, ['binaries/shiwei-ai-worker']);
  assert.deepEqual(mac.bundle.targets, ['app', 'dmg']);
  assert.equal(mac.bundle.macOS.minimumSystemVersion, '14.0');
  assert.equal(mac.bundle.macOS.signingIdentity, '-');
  assert.deepEqual(mac.bundle.macOS.files, { 'MacOS/_internal': 'binaries/macos/_internal' });
  assert.deepEqual(mac.bundle.externalBin, ['binaries/macos/shiwei-ai-worker']);
  // JSON Merge Patch null removes the Windows resource mapping rather than
  // duplicating it under Mac Resources, where the PyInstaller bootloader cannot find it.
  assert.equal(mac.bundle.resources, null);
  assert.equal(mac.plugins.updater.pubkey, '');
  assert.deepEqual(mac.plugins.updater.endpoints, []);
});

test('test artifact names cannot trigger the v* stable tag workflow', () => {
  const sha = 'a'.repeat(40);
  const names = artifactNames('0.3.2', sha);
  assert.equal(names.tag, 'mac-test-v0.3.2-aaaaaaaaaaaa');
  assert.equal(names.filename, 'Shiwei_0.3.2_macos_arm64_test_aaaaaaaaaaaa.dmg');
  for (const [v, s] of [['v0.3.2', sha], ['0.3.2', '../secret'], ['0.3.2-beta', sha]]) assert.throws(() => artifactNames(v, s));
});

test('generated staging paths cannot target workspace or parent directories', () => {
  assert.equal(contained(root, join(root, 'apps/desktop/src-tauri/binaries/macos')), true);
  assert.equal(contained(root, root), false);
  assert.equal(contained(root, join(root, '..')), false);
  assert.equal(contained(root, join(root, '../other-project')), false);
});

test('Mac workflow keeps publishing separate from stable rollout and secrets out of builds', () => {
  const workflow = readFileSync(join(root, '.github/workflows/mac-test.yml'), 'utf8');
  assert.match(workflow, /workflow_dispatch:/);
  assert.match(workflow, /runs-on: macos-14/);
  assert.match(workflow, /SHIWEI_TELEMETRY_DISABLED: '1'/);
  assert.match(workflow, /--prerelease/);
  assert.match(workflow, /--latest=false/);
  assert.doesNotMatch(workflow, /notify-release|SHIWEI_RELEASE_PUBLISH_TOKEN|TAURI_SIGNING_PRIVATE_KEY|--clobber/);
  assert.match(workflow, /environment: release/);
});
