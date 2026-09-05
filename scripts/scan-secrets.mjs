import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import { root } from './version.mjs';
// Scan source, workflows, docs, fixtures and configs, including hidden files. Never print matches.
const ignored = new Set(['.git', '.playwright-cli', 'node_modules', 'target', '.venv', 'build', 'dist', 'output', 'out', '__pycache__', '.pytest_cache', 'binaries']);
const textExtensions = new Set(['.ts', '.tsx', '.js', '.mjs', '.json', '.py', '.rs', '.toml', '.md', '.yml', '.yaml', '.ps1', '.sh', '.txt', '.sql', '.env', '.example']);
const patterns = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
  /(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|phx_[A-Za-z0-9]{24,})/,
  /(?:TAURI_SIGNING_PRIVATE_KEY|SENTRY_AUTH_TOKEN|CERTIFICATE_PASSWORD|POSTHOG_PERSONAL_API_KEY)\s*[:=]\s*["'](?!\$|<|\{|\s*["']|placeholder|your[-_])[A-Za-z0-9+/=_-]{16,}["']/i,
  /untrusted comment: (?:r?sign|encrypted|tauri).*secret key/i,
];
let count = 0; const findings = [];
function visit(directory) {
  for (const item of readdirSync(directory, { withFileTypes: true })) {
    if (item.isSymbolicLink()) continue;
    const path = join(directory, item.name);
    if (item.isDirectory()) { if (!ignored.has(item.name) && !item.name.startsWith('.venv')) visit(path); continue; }
    if (/\.(?:key|pfx|p12|pem)$/.test(item.name) && !item.name.endsWith('.pub')) { findings.push(`${relative(root, path)}: credential file`); continue; }
    if (!textExtensions.has(extname(path)) && !item.name.startsWith('.env')) continue;
    if (item.name === 'scan-secrets.mjs') continue;
    const content = readFileSync(path, 'utf8'); count++;
    const encodedSecret = [...content.matchAll(/[A-Za-z0-9+/]{80,}={0,2}/g)].some(([value]) =>
      /untrusted comment: [^\r\n]*secret key/i.test(Buffer.from(value, 'base64').toString('utf8')));
    if (encodedSecret || patterns.some((pattern) => pattern.test(content))) findings.push(`${relative(root, path)}: potential credential`);
  }
}
visit(root);
if (findings.length) { console.error(findings.join('\n')); process.exitCode = 1; }
else console.log(`Secret pattern scan passed (${count} source/config/document files; generated/runtime directories excluded).`);
