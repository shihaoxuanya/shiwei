import { cpSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const target = 'aarch64-apple-darwin';
export const stage = join(root, 'apps/desktop/src-tauri/binaries/macos');
export const appPath = join(root, `apps/desktop/src-tauri/target/${target}/release/bundle/macos/拾微.app`);
const project = join(root, 'services/ai-worker');
const output = join(root, 'output/mac-test');

export function requireMac(host = process.platform, arch = process.arch, version) {
  if (host !== 'darwin' || arch !== 'arm64') throw new Error('Mac test builds require a native Apple Silicon macOS runner; no cross-compilation or Rosetta.');
  const actual = version ?? execFileSync('sw_vers', ['-productVersion'], { encoding: 'utf8' }).trim();
  if (!/^\d+\.\d+(?:\.\d+)?$/.test(actual) || Number(actual.split('.')[0]) < 14) throw new Error('macOS 14 or newer is required.');
  return actual;
}

export function contained(parent, candidate) {
  const path = relative(resolve(parent), resolve(candidate));
  return path !== '' && path !== '..' && !path.startsWith(`..${sep}`) && !isAbsolute(path);
}

export function assertRuntimeLinks(directory) {
  const base = realpathSync(directory);
  function visit(path) {
    if (lstatSync(path).isSymbolicLink()) {
      const resolved = realpathSync(path); // also rejects broken links
      if (resolved !== base && !contained(base, resolved)) throw new Error('Worker runtime contains a symlink outside its bundle.');
    } else if (lstatSync(path).isDirectory()) {
      for (const name of readdirSync(path)) visit(join(path, name));
    }
  }
  visit(directory);
}

export function artifactNames(version, sha) {
  if (!/^\d+\.\d+\.\d+$/.test(version) || !/^[a-f0-9]{40}$/.test(sha)) throw new Error('Expected a release version and an exact source commit SHA.');
  const suffix = sha.slice(0, 12);
  return { tag: `mac-test-v${version}-${suffix}`, filename: `Shiwei_${version}_macos_arm64_test_${suffix}.dmg` };
}

function run(program, args, cwd = root) {
  execFileSync(program, args, { cwd, stdio: 'inherit', env: { ...process.env, MACOSX_DEPLOYMENT_TARGET: '14.0', CARGO_BUILD_JOBS: '2' } });
}

function verify(location) {
  run('uv', ['run', '--project', project, '--frozen', 'python', 'scripts/verify-macos-bundle.py', String(location)]);
}

function worker() {
  requireMac();
  run('uv', ['run', '--frozen', 'python', '-m', 'PyInstaller', '--noconfirm', 'shiwei-ai.spec'], project);
  const source = join(project, 'dist/shiwei-ai-worker');
  if (!existsSync(join(source, '_internal')) || !existsSync(join(source, 'shiwei-ai-worker'))) throw new Error('Incomplete PyInstaller onedir output.');
  assertRuntimeLinks(source);
  verify(source);
  // Only this dedicated, generated macOS staging directory may be replaced.
  if (!contained(root, stage) || (existsSync(stage) && lstatSync(stage).isSymbolicLink())) throw new Error('Unsafe Mac staging directory.');
  if (existsSync(stage)) rmSync(stage, { recursive: true });
  mkdirSync(stage, { recursive: true });
  cpSync(join(source, '_internal'), join(stage, '_internal'), { recursive: true, verbatimSymlinks: true });
  cpSync(join(source, 'shiwei-ai-worker'), join(stage, `shiwei-ai-worker-${target}`));
  console.log('Native ARM64 Worker staged with its complete runtime.');
}

function app() {
  requireMac();
  if (!existsSync(join(stage, `shiwei-ai-worker-${target}`))) throw new Error('Build the native Worker before the app.');
  run('pnpm', ['--dir', 'apps/desktop', 'run', 'tauri', 'build', '--target', target]);
  verify(appPath);
}

function artifacts() {
  requireMac();
  verify(appPath);
  const version = readFileSync(join(root, 'VERSION'), 'utf8').trim();
  const sha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
  if (process.env.GITHUB_SHA && process.env.GITHUB_SHA !== sha) throw new Error('Checkout does not match the CI commit.');
  if (execFileSync('git', ['status', '--porcelain', '--untracked-files=no'], { cwd: root, encoding: 'utf8' }).trim()) throw new Error('Refusing artifacts from modified tracked sources.');
  const { tag, filename } = artifactNames(version, sha);
  const bundleDir = join(root, `apps/desktop/src-tauri/target/${target}/release/bundle/dmg`);
  const images = readdirSync(bundleDir).filter(name => name.endsWith('.dmg'));
  if (images.length !== 1) throw new Error('Expected one freshly built DMG; stale/mixed bundles are not publishable.');
  // Never overwrite a previous test build.
  const destination = join(output, tag);
  mkdirSync(output, { recursive: true });
  mkdirSync(destination);
  cpSync(join(bundleDir, images[0]), join(destination, filename));
  const digest = createHash('sha256').update(readFileSync(join(destination, filename))).digest('hex');
  writeFileSync(join(destination, 'SHA256SUMS.txt'), `${digest}  ${filename}\n`);
  cpSync(join(root, 'docs/MAC-TEST.md'), join(destination, 'MAC-TEST.md'));
  writeFileSync(join(destination, 'release-notes.md'), `# 拾微 ${version} Mac 内测\n\nApple Silicon（含 M1 Pro），macOS 14+。Ad-hoc 临时签名，未经 Apple 公证，首次打开可能需要单独允许。\n\n不支持 Intel，不包含自动更新，不改变 Windows 发布。\n\n源码提交：${sha}\n\n请先阅读随包 MAC-TEST.md；CI 检查不能代替测试用户的首次安装验收。\n`);
  console.log(JSON.stringify({ tag, filename, sha256: digest, artifacts: relative(root, destination) }));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const action = { worker, app, artifacts }[process.argv[2]];
    if (!action) throw new Error('Expected worker, app, or artifacts.');
    action();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
