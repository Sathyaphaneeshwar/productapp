import { app, BrowserWindow, Menu, Tray, nativeImage, dialog } from 'electron';
import path from 'path';
import fs from 'fs';
import { spawn, exec } from 'child_process';
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

// GitHub token for private repo access (read-only, contents:read permission)
const GH_UPDATE_TOKEN = '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';
autoUpdater.requestHeaders = { 'Authorization': `token ${GH_UPDATE_TOKEN}` };

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

// Backend port
const BACKEND_PORT = 5001;

// Kill any process using the backend port
async function killProcessOnPort(port) {
  log(`killProcessOnPort: Starting for port ${port}`);

  return new Promise((resolve) => {
    try {
      if (process.platform === 'win32') {
        // Windows command to find and kill process on port
        exec(`netstat -ano | findstr :${port}`, (err, stdout) => {
          if (err || !stdout) {
            log(`killProcessOnPort: No process on port ${port} (Windows)`);
            resolve();
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
          log(`killProcessOnPort: Found ${pids.size} processes to kill`);
          // Kill each PID
          pids.forEach(pid => {
            try {
              exec(`taskkill /PID ${pid} /F`, () => { });
            } catch (e) {
              log(`killProcessOnPort: Error killing PID ${pid}: ${e.message}`);
            }
          });
          setTimeout(resolve, 500);
        });
      } else {
        // Mac/Linux command to find and kill process on port
        exec(`lsof -i :${port} -t`, (err, stdout) => {
          if (err || !stdout) {
            log(`killProcessOnPort: No process on port ${port} (Mac/Linux)`);
            resolve();
            return;
          }
          const pids = stdout.trim().split('\n');
          log(`killProcessOnPort: Found ${pids.length} processes to kill: ${pids.join(', ')}`);
          pids.forEach(pid => {
            if (pid) {
              try {
                exec(`kill -9 ${pid}`, () => { });
              } catch (e) {
                log(`killProcessOnPort: Error killing PID ${pid}: ${e.message}`);
              }
            }
          });
          setTimeout(resolve, 500);
        });
      }
    } catch (e) {
      log(`killProcessOnPort: Exception caught: ${e.message}`);
      resolve(); // Resolve anyway to not block
    }
  });
}

const logPath = path.join(app.getPath('userData'), 'app.log');
const logStream = fs.createWriteStream(logPath, { flags: 'a' });

function log(message) {
  const timestamp = new Date().toISOString();
  const msg = `${timestamp}: ${message}\n`;
  console.log(msg); // Console still works in dev
  try {
    logStream.write(msg);
  } catch (e) {
    // ignore logging errors
  }
}

async function startBackend() {
  const executableName = process.platform === 'win32' ? 'backend-app.exe' : 'backend-app';

  const BACKEND_EXE = app.isPackaged
    ? path.join(process.resourcesPath, 'python', 'backend-app', executableName)
    : path.join(__dirname, '..', 'backend', 'dist', 'backend-app', executableName);

  const BACKEND_DIR = path.dirname(BACKEND_EXE);

  log(`Starting backend from: ${BACKEND_EXE}`);
  log(`Backend working directory: ${BACKEND_DIR}`);

  if (!fs.existsSync(BACKEND_EXE)) {
    log(`ERROR: Backend executable not found at ${BACKEND_EXE}`);
    return;
  }

  // Kill any process blocking our port first
  await killProcessOnPort(BACKEND_PORT);

  try {
    // Ensure proper environment for macOS
    const spawnEnv = {
      ...process.env,
      PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    };

    log(`Starting spawn with env PATH: ${spawnEnv.PATH?.substring(0, 100)}...`);

    backendProcess = spawn(BACKEND_EXE, [], {
      cwd: BACKEND_DIR, // IMPORTANT: Run in the directory of the executable
      detached: false,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      env: spawnEnv
    });

    log(`Spawn called, process object created: ${!!backendProcess}`);
    log(`Backend PID: ${backendProcess?.pid || 'undefined'}`);

    backendProcess.stdout.on('data', (data) => {
      log(`[Backend]: ${data}`);
    });

    backendProcess.stderr.on('data', (data) => {
      log(`[Backend ERROR]: ${data}`);
    });

    backendProcess.on('error', (err) => {
      log(`Failed to spawn backend: ${err.message}`);
      log(`Error stack: ${err.stack}`);
    });

    backendProcess.on('close', (code, signal) => {
      log(`Backend process exited with code ${code}, signal ${signal}`);
    });

    backendProcess.on('exit', (code, signal) => {
      log(`Backend process exit event: code ${code}, signal ${signal}`);
    });

    if (backendProcess.pid) {
      log(`Backend started successfully with PID: ${backendProcess.pid}`);
    } else {
      log(`WARNING: Backend process created but no PID assigned`);
    }
  } catch (e) {
    log(`Exception starting backend: ${e.message}`);
    log(`Exception stack: ${e.stack}`);
  }
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

  // Open DevTools in development or if requested (optional for debugging)
  // mainWindow.webContents.openDevTools(); 

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

  // Create window FIRST so user sees something
  createWindow();
  setupTray();
  app.setLoginItemSettings({ openAtLogin: true, enabled: true });

  // Add execute permissions for Mac/Linux
  if (app.isPackaged && process.platform !== 'win32') {
    const executableName = 'backend-app';
    const backendPath = path.join(process.resourcesPath, 'python', 'backend-app', executableName);
    if (fs.existsSync(backendPath)) {
      try {
        fs.chmodSync(backendPath, '755');
        console.log('Set permissions for backend executable');
      } catch (err) {
        console.error('Failed to set permissions:', err);
      }
    }
  }

  // Start backend
  await startBackend();

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
