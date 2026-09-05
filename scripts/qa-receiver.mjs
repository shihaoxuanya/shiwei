import { createServer } from "node:http";
import { mkdirSync, writeFileSync } from "node:fs";

const server = createServer(async (request, response) => {
  response.setHeader("access-control-allow-origin", "*");
  response.setHeader("access-control-allow-methods", "POST,OPTIONS");
  if (request.method === "OPTIONS") { response.writeHead(204); response.end(); return; }
  if (request.method !== "POST" || request.url !== "/capture") { response.writeHead(404); response.end(); return; }
  const chunks = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  mkdirSync("design", { recursive: true });
  writeFileSync("design/implementation-knowledge.png", Buffer.from(Buffer.concat(chunks).toString("utf8"), "base64"));
  response.writeHead(204, { "access-control-allow-origin": "*" }); response.end();
  server.close();
});
server.listen(4174, "127.0.0.1", () => process.stdout.write("QA receiver ready\n"));
