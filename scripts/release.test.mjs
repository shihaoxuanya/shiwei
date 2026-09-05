import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseVersion, validateVersion, root } from './version.mjs';
import { artifactBaseUrl, releaseNotes, updateMetadata, validateArtifactDirectory } from './release-artifacts.mjs';
import { notifyReady } from './notify-release.mjs';
test('CI only registers READY, authenticates and refuses redirects', async () => {
  let calls = 0;
  await notifyReady('https://zhishimanghe.com', 'test-only', { version: '0.3.1' }, async (url, options) => {
    calls++;
    assert.equal(url.pathname, '/internal/releases/artifact-ready');
    assert.equal(options.headers.Authorization, 'Bearer test-only');
    assert.equal(options.redirect, 'error');
    assert.deepEqual(JSON.parse(options.body), { version: '0.3.1' });
    return Response.json({ version: '0.3.1', status: 'READY' });
  });
  assert.equal(calls, 1);
});
test('CI registration rejects unsafe origins and cannot claim failed registration is READY', async () => {
  for (const origin of ['http://zhishimanghe.com', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com/?token=x']) {
    await assert.rejects(notifyReady(origin, 'test-only', {}, () => assert.fail('must not request')));
  }
  await assert.rejects(notifyReady('https://example.com', '', {}, () => assert.fail('must not request')));
  await assert.rejects(notifyReady('https://example.com', 'test-only', { version: '0.3.1' }, async () => new Response(null, { status: 401 })));
  await assert.rejects(notifyReady('https://example.com', 'test-only', { version: '0.3.1' }, async () => Response.json({ version: '0.3.1', status: 'PUBLISHED' })));
});
test('split repository artifacts never fall back to the private source repository', () => {
  const config = JSON.parse(readFileSync(resolve(root, 'release.config.json'), 'utf8'));
  assert.equal(artifactBaseUrl(config, { GITHUB_REPOSITORY: config.sourceRepository }, '0.3.1'), 'https://github.com/shihaoxuanya/shiwei-releases/releases/download/v0.3.1');
  assert.throws(() => artifactBaseUrl(config, { SHIWEI_RELEASE_REPOSITORY: config.sourceRepository }, '0.3.1'));
  assert.throws(() => artifactBaseUrl({}, { GITHUB_REPOSITORY: config.sourceRepository }, '0.3.1'));
});
test('artifact repository rejects malformed destinations and unstable versions', () => {
  for (const repository of ['https://github.com/o/r', 'o/r/extra', 'o/r?token=value', '../r']) {
    assert.throws(() => artifactBaseUrl({ artifactRepository: repository }, {}, '0.3.1'));
  }
  assert.throws(() => artifactBaseUrl({ artifactRepository: 'owner/releases' }, {}, '0.4.0-beta.1'));
});
test('semantic versions reject ambiguous numbers and syntax', () => {
  for (const value of ['0.3.0', '1.0.0', '0.4.0-beta.2', '1.0.0+build.1']) assert.equal(parseVersion(value).version, value);
  for (const value of ['v0.3.0', '0.3', '01.3.0', '0.3.0-beta.01', '0.3.0\n', 'garbage']) assert.throws(() => parseVersion(value));
});
test('all release versions match VERSION and tag', () => {
  const v = readFileSync(resolve(root, 'VERSION'), 'utf8').trim();
  assert.equal(validateVersion(root, `v${v}`), v);
  assert.throws(() => validateVersion(root, 'v999.0.0'));
});
test('curated notes exclude other releases and missing notes fail', () => {
  assert.equal(releaseNotes('# Changes\n\n## 0.3.0\n\n- 更新\n\n## 0.2.2\nOld', '0.3.0'), '- 更新');
  assert.throws(() => releaseNotes('## 0.2.2\nold', '0.3.0'));
});
test('official metadata requires HTTPS, stable version, and embedded signature contents', () => {
  const sig = Buffer.from('untrusted comment: signature from a test fixture\nfixture').toString('base64');
  const result = updateMetadata('0.3.0', 'https://example.com/installer.exe', sig, '更新');
  assert.equal(result.platforms['windows-x86_64'].signature, sig);
  for (const [version, url, signature] of [['0.4.0-beta.2', 'https://example.com/a', sig], ['0.3.0', 'http://example.com/a', sig], ['0.3.0', 'https://example.com/a', 'bad']]) assert.throws(() => updateMetadata(version, url, signature, '更新'));
});
test('release config contains no enabled analytics or fabricated update endpoint', () => {
  const config = JSON.parse(readFileSync(resolve(root, 'release.config.json'), 'utf8'));
  assert.equal(config.channel, 'stable');
  assert.ok(!('privateKey' in config));
});
test('release staging refuses stale installers and unexpected uploads', () => {
  validateArtifactDirectory(['Shiwei_0.3.0_x64-setup.exe', 'latest.json'], '0.3.0');
  assert.throws(() => validateArtifactDirectory(['Shiwei_0.2.2_x64-setup.exe'], '0.3.0'));
  assert.throws(() => validateArtifactDirectory(['private.key'], '0.3.0'));
});
