const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electronUpdater', {
    // Check for updates
    checkForUpdates: () => ipcRenderer.invoke('check-for-updates'),

    // Install downloaded update
    installUpdate: () => ipcRenderer.invoke('install-update'),

    // Listen for update status changes
    onUpdateStatus: (callback) => {
        // Remove any existing listeners first
        ipcRenderer.removeAllListeners('update-status');
        ipcRenderer.on('update-status', (_event, data) => callback(data));
    },

    // Get current app version
    getVersion: () => ipcRenderer.invoke('get-app-version'),

    // Check if running in Electron
    isElectron: true
});
