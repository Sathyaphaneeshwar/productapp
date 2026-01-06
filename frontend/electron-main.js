import { app, BrowserWindow, Menu, Tray, nativeImage, dialog } from 'electron';
import path from 'path';
import fs from 'fs';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import http from 'http';
import pkg from 'electron-updater';
const { autoUpdater } = pkg;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let tray;
let mainWindow;
let backendProcess;
let isQuitting = false;

// Configure auto-updater
autoUpdater.autoDownload = true;
autoUpdater.autoInstallOnAppQuit = true;

autoUpdater.on('update-available', () => {
  console.log('Update available, downloading...');
});

autoUpdater.on('update-downloaded', () => {
  dialog.showMessageBox({
    type: 'info',
    title: 'Update Ready',
    message: 'A new version has been downloaded. Restart the app to apply the update.',
    buttons: ['Restart Now', 'Later']
  }).then((result) => {
    if (result.response === 0) {
      isQuitting = true;
      autoUpdater.quitAndInstall();
    }
  });
});

autoUpdater.on('error', (err) => {
  console.log('Auto-updater error:', err);
});

const BACKEND_EXE = app.isPackaged
  ? path.join(process.resourcesPath, 'python', 'backend-app', 'backend-app.exe')
  : path.join(__dirname, '..', 'backend', 'dist', 'backend-app', 'backend-app.exe');

const BACKEND_PORT = 5001;

// Kill any process using the backend port
async function killProcessOnPort(port) {
  return new Promise((resolve) => {
    const { exec } = require('child_process');
    // Windows command to find and kill process on port
    exec(`netstat -ano | findstr :${port}`, (err, stdout) => {
      if (err || !stdout) {
        resolve(); // No process found
        return;
      }
      // Extract PID from netstat output
      const lines = stdout.trim().split('\n');
      const pids = new Set();
      lines.forEach(line => {
        const parts = line.trim().split(/\s+/);
        const pid = parts[parts.length - 1];
        if (pid && !isNaN(pid)) {
          pids.add(pid);
        }
      });
      // Kill each PID
      pids.forEach(pid => {
        try {
          exec(`taskkill /PID ${pid} /F`, () => { });
        } catch (e) { }
      });
      setTimeout(resolve, 500); // Wait for processes to die
    });
  });
}

async function startBackend() {
  if (!fs.existsSync(BACKEND_EXE)) {
    console.error('Backend executable not found at', BACKEND_EXE);
    return;
  }

  // Kill any process blocking our port first
  await killProcessOnPort(BACKEND_PORT);

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
  const url = `http://127.0.0.1:${BACKEND_PORT}/api/watchlist`;

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
  await startBackend();
  setupTray();
  createWindow();
  app.setLoginItemSettings({ openAtLogin: true, enabled: true });

  await waitForBackend();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.reloadIgnoringCache();
    mainWindow.show();
  }

  // Check for updates (only in production)
  if (app.isPackaged) {
    autoUpdater.checkForUpdates();
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
