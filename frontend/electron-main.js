import { app, BrowserWindow, Menu, Tray, nativeImage, dialog, ipcMain } from 'electron';
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

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
}

app.on('second-instance', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }
});

// Update status tracking
let updateStatus = 'idle'; // 'idle', 'checking', 'available', 'downloading', 'ready', 'error'
let updateInfo = null;

// Configure auto-updater
autoUpdater.autoDownload = true;
autoUpdater.autoInstallOnAppQuit = true;

// Helper to send update status to renderer
function sendUpdateStatus(status, info = null) {
  updateStatus = status;
  updateInfo = info;
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('update-status', { status, info });
  }
}

autoUpdater.on('checking-for-update', () => {
  log('Checking for update...');
  sendUpdateStatus('checking');
});

autoUpdater.on('update-available', (info) => {
  log('Update available: ' + JSON.stringify(info));
  sendUpdateStatus('downloading', info);
});

autoUpdater.on('update-not-available', (info) => {
  log('Update not available');
  sendUpdateStatus('idle', info);
});

autoUpdater.on('download-progress', (progress) => {
  log(`Download progress: ${progress.percent}%`);
  sendUpdateStatus('downloading', { percent: progress.percent });
});

autoUpdater.on('update-downloaded', (info) => {
  log('Update downloaded');
  sendUpdateStatus('ready', info);
});

autoUpdater.on('error', (err) => {
  log('Auto-updater error: ' + err.message);
  sendUpdateStatus('error', { message: err.message });
});

// Backend port
const BACKEND_PORT = 5001;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function execAsync(command) {
  return new Promise((resolve) => {
    exec(command, (error, stdout, stderr) => {
      resolve({ error, stdout, stderr });
    });
  });
}

async function listPidsOnPort(port) {
  try {
    if (process.platform === 'win32') {
      const { error, stdout } = await execAsync(`netstat -ano | findstr :${port}`);
      if (error || !stdout) return [];

      const pids = new Set();
      stdout.trim().split('\n').forEach((line) => {
        const parts = line.trim().split(/\s+/);
        const pid = parts[parts.length - 1];
        if (pid && !isNaN(pid)) {
          pids.add(pid);
        }
      });
      return [...pids];
    }

    const { error, stdout } = await execAsync(`lsof -nP -iTCP:${port} -sTCP:LISTEN -t`);
    if (error || !stdout) return [];
    return stdout
      .trim()
      .split('\n')
      .map((pid) => pid.trim())
      .filter(Boolean);
  } catch (e) {
    log(`listPidsOnPort error: ${e.message}`);
    return [];
  }
}

async function isPidAlive(pid) {
  if (!pid) return false;

  if (process.platform === 'win32') {
    const { error, stdout } = await execAsync(`tasklist /FI "PID eq ${pid}" /NH`);
    if (error) return false;
    return stdout && !stdout.includes('No tasks are running');
  }

  try {
    process.kill(Number(pid), 0);
    return true;
  } catch (e) {
    return e.code === 'EPERM';
  }
}

async function stopPidGracefully(pid, graceMs = 4000) {
  if (!pid) return;

  try {
    if (process.platform === 'win32') {
      await execAsync(`taskkill /PID ${pid} /T`);
    } else {
      process.kill(Number(pid), 'SIGTERM');
    }
  } catch (e) {
    log(`Graceful stop failed for PID ${pid}: ${e.message}`);
  }

  const deadline = Date.now() + graceMs;
  while (Date.now() < deadline) {
    if (!(await isPidAlive(pid))) {
      return;
    }
    await sleep(200);
  }

  log(`PID ${pid} did not exit after SIGTERM, forcing stop`);
  try {
    if (process.platform === 'win32') {
      await execAsync(`taskkill /PID ${pid} /T /F`);
    } else {
      process.kill(Number(pid), 'SIGKILL');
    }
  } catch (e) {
    log(`Force stop failed for PID ${pid}: ${e.message}`);
  }
}

async function stopProcessesOnPort(port) {
  const pids = await listPidsOnPort(port);
  if (pids.length === 0) {
    return;
  }

  log(`Port ${port} is in use by PID(s): ${pids.join(', ')}`);
  for (const pid of pids) {
    await stopPidGracefully(pid);
  }
}

async function isBackendResponsive(timeoutMs = 1200) {
  const url = `http://127.0.0.1:${BACKEND_PORT}/api/poll/status`;
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(res.statusCode && res.statusCode < 500);
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve(false);
    });
    req.on('error', () => resolve(false));
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

function resolveBackendExecutable() {
  const executableName = process.platform === 'win32' ? 'backend-app.exe' : 'backend-app';

  const sourceDir = app.isPackaged
    ? path.join(process.resourcesPath, 'python', 'backend-app')
    : path.join(__dirname, '..', 'backend', 'dist', 'backend-app');

  let backendExe = path.join(sourceDir, executableName);

  if (app.isPackaged && process.platform === 'darwin') {
    const targetDir = path.join(app.getPath('userData'), 'backend-app');
    const versionFile = path.join(targetDir, '.backend-version');
    const appVersion = app.getVersion();

    let needsCopy = !fs.existsSync(targetDir);
    if (!needsCopy) {
      try {
        const currentVersion = fs.readFileSync(versionFile, 'utf8').trim();
        needsCopy = currentVersion !== appVersion;
      } catch (err) {
        needsCopy = true;
      }
    }

    if (needsCopy) {
      log(`Preparing backend copy for macOS at ${targetDir}`);
      const stagedTargetDir = `${targetDir}.staged`;
      const stagedVersionFile = path.join(stagedTargetDir, '.backend-version');
      try {
        fs.rmSync(stagedTargetDir, { recursive: true, force: true });
        fs.cpSync(sourceDir, stagedTargetDir, { recursive: true });
        const stagedExecutable = path.join(stagedTargetDir, executableName);
        if (!fs.existsSync(stagedExecutable)) {
          throw new Error(`Staged backend executable missing at ${stagedExecutable}`);
        }
        fs.writeFileSync(stagedVersionFile, appVersion);
        fs.rmSync(targetDir, { recursive: true, force: true });
        fs.renameSync(stagedTargetDir, targetDir);
      } catch (err) {
        log(`Failed to copy backend to userData: ${err.message}`);
        try {
          fs.rmSync(stagedTargetDir, { recursive: true, force: true });
        } catch {
          // ignore cleanup errors
        }
      }
    }

    const copiedExe = path.join(targetDir, executableName);
    if (fs.existsSync(copiedExe)) {
      backendExe = copiedExe;
      try {
        fs.chmodSync(backendExe, 0o755);
      } catch (err) {
        log(`Failed to set execute permissions: ${err.message}`);
      }
    }
  }

  return backendExe;
}

async function startBackend() {
  const existingBackendHealthy = await isBackendResponsive();
  if (existingBackendHealthy) {
    log('Detected healthy backend already running; skipping restart');
    return true;
  }

  // Only stop listeners when the backend is not healthy.
  await stopProcessesOnPort(BACKEND_PORT);

  const BACKEND_EXE = resolveBackendExecutable();

  const BACKEND_DIR = path.dirname(BACKEND_EXE);

  log(`Starting backend from: ${BACKEND_EXE}`);
  log(`Backend working directory: ${BACKEND_DIR}`);

  if (!fs.existsSync(BACKEND_EXE)) {
    log(`ERROR: Backend executable not found at ${BACKEND_EXE}`);
    return false;
  }

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
      backendProcess = null;
    });

    backendProcess.on('exit', (code, signal) => {
      log(`Backend process exit event: code ${code}, signal ${signal}`);
    });

    if (backendProcess.pid) {
      log(`Backend started successfully with PID: ${backendProcess.pid}`);
      return true;
    } else {
      log(`WARNING: Backend process created but no PID assigned`);
      return false;
    }
  } catch (e) {
    log(`Exception starting backend: ${e.message}`);
    log(`Exception stack: ${e.stack}`);
    return false;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
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
  if (!gotSingleInstanceLock) {
    return;
  }

  Menu.setApplicationMenu(null);

  // Create window FIRST so user sees something
  createWindow();
  setupTray();
  app.setLoginItemSettings({ openAtLogin: true, enabled: true });

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

  // IPC handlers for update button
  ipcMain.handle('check-for-updates', async () => {
    if (!app.isPackaged) {
      sendUpdateStatus('idle', { message: 'Updates only work in packaged app' });
      return { status: 'dev-mode' };
    }
    try {
      const result = await autoUpdater.checkForUpdates();
      return { status: 'checking', result };
    } catch (err) {
      sendUpdateStatus('error', { message: err.message });
      return { status: 'error', message: err.message };
    }
  });

  ipcMain.handle('install-update', () => {
    if (updateStatus === 'ready') {
      isQuitting = true;
      autoUpdater.quitAndInstall();
      return { status: 'installing' };
    }
    return { status: 'not-ready' };
  });

  ipcMain.handle('get-app-version', () => {
    return app.getVersion();
  });
});

app.on('window-all-closed', (event) => {
  // Keep the tray + backend alive even if window is closed
  event.preventDefault();
});

app.on('before-quit', () => {
  isQuitting = true;
  if (backendProcess?.pid) {
    stopPidGracefully(String(backendProcess.pid)).catch((err) => {
      console.error('Failed to stop backend process', err);
    });
  }
});
