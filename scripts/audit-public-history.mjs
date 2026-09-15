// Read-only audit of every reachable Git blob. Never print matching values.
import { execFileSync } from 'node:child_process';
const repository = process.argv[2];
if (!repository) throw new Error('Provide a Git repository to audit');
const git = args => execFileSync('git', ['--git-dir='+repository,...args], {windowsHide:true,maxBuffer:128*1024*1024});
const lines=git(['rev-list','--objects','--all']).toString('utf8').trim().split('\n');
const paths=new Map(lines.map(line=>{const i=line.indexOf(' ');return [i<0?line:line.slice(0,i),i<0?'':line.slice(i+1)];}));
const data=execFileSync('git',['--git-dir='+repository,'cat-file','--batch'],{input:[...paths.keys()].join('\n')+'\n',windowsHide:true,maxBuffer:128*1024*1024});
const patterns=[
 ['private-key',/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/],
 ['api-token',/(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})/],
 ['provider-token',/\b[a-f0-9]{32}\.[A-Za-z0-9]{16,}\b/i],
 ['credential-file',/untrusted comment: (?:encrypted|minisign|tauri)[^\r\n]*secret key/i],
 ['personal-email',/\b[0-9]{5,12}@qq\.com\b/i],
];
const findings=[];let offset=0,blobs=0;
while(offset<data.length){const end=data.indexOf(10,offset);if(end<0)break;const [sha,type,n]=data.subarray(offset,end).toString().split(' ');const size=Number(n);const raw=data.subarray(end+1,end+1+size);offset=end+size+2;if(type!=='blob')continue;blobs++;const path=paths.get(sha);const text=raw.toString('utf8');
 for(const [kind,re] of patterns)if(re.test(text))findings.push({sha,path,kind});
 if(/\.(?:key|pfx|p12|dpapi|sqlite|db)$|(?:^|\/)\.env$/.test(path))findings.push({sha,path,kind:'sensitive-file-name'});
 for(const [encoded] of text.matchAll(/[A-Za-z0-9+/]{80,}={0,2}/g))if(/untrusted comment: [^\r\n]*secret key/i.test(Buffer.from(encoded,'base64').toString('utf8')))findings.push({sha,path,kind:'encoded-private-key'});
}
console.log(JSON.stringify({commits:Number(git(['rev-list','--all','--count']).toString()),blobs,findings},null,2));
if(findings.length)process.exitCode=1;
