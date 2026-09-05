import { app, BrowserWindow } from "electron";
import { writeFileSync } from "node:fs";

app.commandLine.appendSwitch("force-device-scale-factor", "1");
app.commandLine.appendSwitch("disable-gpu");

app.whenReady().then(async () => {
  process.stdout.write("Electron QA capture started\n");
  const window = new BrowserWindow({ width: 1440, height: 1028, useContentSize: true, frame: false, show: false, webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
  window.webContents.on("console-message", (_event, level, message) => process.stdout.write(`[renderer:${level}] ${message}\n`));
  await window.loadURL("http://127.0.0.1:5173");
  await window.webContents.executeJavaScript("localStorage.clear()");
  await window.webContents.reload();
  let ready = false;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (await window.webContents.executeJavaScript("Boolean(document.querySelector('.knowledge-layout'))")) { ready = true; break; }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  await new Promise((resolve) => setTimeout(resolve, 400));
  process.stdout.write(`Renderer ready: ${ready}; ${await window.webContents.executeJavaScript("document.body.innerText.slice(0,160)")}\n`);
  const image = await window.webContents.capturePage();
  writeFileSync("design/implementation-knowledge.png", image.toPNG());
  process.stdout.write("Electron QA capture saved\n");
  window.destroy();
  app.quit();
}).catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : error}\n`);
  app.exit(1);
});
