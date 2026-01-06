import { app, BrowserWindow, Menu, Tray, nativeImage } from 'electron';
import path from 'path';
import fs from 'fs';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import http from 'http';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let tray;
let mainWindow;
let backendProcess;
let isQuitting = false;

const BACKEND_EXE = app.isPackaged
  ? path.join(process.resourcesPath, 'python', 'backend-app', 'backend-app.exe')
  : path.join(__dirname, '..', 'backend', 'dist', 'backend-app', 'backend-app.exe');

function startBackend() {
  if (!fs.existsSync(BACKEND_EXE)) {
    console.error('Backend executable not found at', BACKEND_EXE);
    return;
  }

  backendProcess = spawn(BACKEND_EXE, [], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true
  });

  backendProcess.unref();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true
    }
  });

  const indexPath = path.join(__dirname, 'dist', 'index.html');
  mainWindow.loadFile(indexPath).then(() => mainWindow?.show());

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow?.hide();
    }
  });
}

function setupTray() {
  const iconPath = path.join(__dirname, 'icon.png');
  const trayImage = nativeImage.createFromPath(iconPath);
  tray = new Tray(trayImage);

  const menu = Menu.buildFromTemplate([
    { label: 'Open', click: () => mainWindow?.show() },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        isQuitting = true;
        app.quit();
      }
    }
  ]);

  tray.setToolTip('Product Gemini');
  tray.setContextMenu(menu);
  tray.on('double-click', () => mainWindow?.show());
}

async function waitForBackend(retries = 20) {
  const url = 'http://127.0.0.1:5000/api/watchlist';

  return new Promise((resolve) => {
    const attempt = (remaining) => {
      const req = http.get(url, () => resolve(true));
      req.on('error', () => {
        if (remaining <= 0) {
          return resolve(false);
        }
        setTimeout(() => attempt(remaining - 1), 500);
      });
    };

    attempt(retries);
  });
}

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  startBackend();
  setupTray();
  createWindow();
  app.setLoginItemSettings({ openAtLogin: true, enabled: true });

  await waitForBackend();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.reloadIgnoringCache();
    mainWindow.show();
  }
});

app.on('window-all-closed', (event) => {
  // Keep the tray + backend alive even if window is closed
  event.preventDefault();
});

app.on('before-quit', () => {
  isQuitting = true;
  if (backendProcess?.pid) {
    try {
      process.kill(backendProcess.pid);
    } catch (err) {
      console.error('Failed to stop backend process', err);
    }
  }
});
