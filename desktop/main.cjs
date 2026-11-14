const { app, BrowserWindow, session } = require("electron");
const { spawn } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");

const backendUrl = "http://127.0.0.1:8001";
const projectRoot = path.resolve(__dirname, "..");
let backendProcess;

app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

function backendIsReady() {
  return new Promise((resolve) => {
    const request = http.get(`${backendUrl}/health`, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.setTimeout(600, () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
  });
}

async function waitForBackend() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (await backendIsReady()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Vimo backend did not start on port 8001.");
}

async function ensureBackend() {
  if (await backendIsReady()) return;
  backendProcess = spawn("uv", ["run", "uvicorn", "app.main:app", "--port", "8001"], {
    cwd: projectRoot,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  await waitForBackend();
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1260,
    height: 860,
    minWidth: 940,
    minHeight: 680,
    backgroundColor: "#f6f7f2",
    title: "Vimo by Aimo",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.loadURL(backendUrl);
}

app.whenReady().then(async () => {
  const allowMicrophone = (permission) => ["media", "microphone", "audioCapture"].includes(permission);
  session.defaultSession.setPermissionCheckHandler((_webContents, permission) => allowMicrophone(permission));
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => callback(allowMicrophone(permission)));
  try {
    await ensureBackend();
    createWindow();
  } catch (error) {
    console.error(error.message);
    app.quit();
  }
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  if (backendProcess && !backendProcess.killed) backendProcess.kill();
});
