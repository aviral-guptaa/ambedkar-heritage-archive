/**
 * The kiosk shell.
 *
 * This is the only part of the project that talks to a display, a touchscreen or
 * a scanner. Everything it does is driven by the archive's own HW_ settings
 * rather than by a hardware model, so the same build runs on a laptop, a
 * touchscreen panel and a single-board computer without a code change.
 *
 * The shell deliberately has no privileged access to the archive. It displays a
 * web page; it does not read the database, and it cannot change what the archive
 * says. Everything a reader sees still comes from the API.
 */
import { app, BrowserWindow, ipcMain, screen, shell } from 'electron'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = fileURLToPath(new URL('.', import.meta.url))

import {
  isExternalLink,
  isInternalUrl,
  permissionDecision,
  readProfile,
  windowOptions,
} from './hardware.js'

/**
 * Hardware capabilities, read from the environment.
 *
 * The rules live in hardware.js so they can be tested without a display; this is
 * only the wiring.
 */
const hardware = readProfile(process.env)

/** Electron will not load a relative preload, so it is resolved once here. */
const PRELOAD = fileURLToPath(new URL('preload.cjs', import.meta.url))

let mainWindow = null
let idleTimer = null
let lastInteraction = Date.now()

function createWindow() {
  const display = screen.getPrimaryDisplay().workAreaSize
  mainWindow = new BrowserWindow({ ...windowOptions(hardware, display, PRELOAD), show: false })
  return attachWindow(mainWindow)
}

function attachWindow(mainWindowRef) {
  mainWindowRef.once('ready-to-show', () => mainWindowRef.show())
  mainWindowRef.on('closed', () => {
    mainWindow = null
  })

  // A kiosk must not be able to wander off the archive. Links to outside
  // sources open in the visitor's own browser instead, so the citation a reader
  // wants to check is reachable without stranding them in an unmaintained page.
  mainWindowRef.webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalLink(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  mainWindowRef.webContents.on('will-navigate', (event, url) => {
    if (!isInternalUrl(url, hardware)) {
      event.preventDefault()
      if (isExternalLink(url)) void shell.openExternal(url)
    }
  })

  // Nothing in a kiosk needs a camera or a microphone by default, and a public
  // machine should not be asking for either unless it was told to.
  mainWindowRef.webContents.session.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permissionDecision(permission, hardware))
  })

  void mainWindowRef.loadURL(hardware.serverUrl)
  return mainWindowRef
}

/**
 * Return the kiosk to the home page after a spell of no touching.
 *
 * This exists so the next visitor finds a clean archive rather than the last
 * search somebody left running. It is a presentation convenience and never
 * changes stored data.
 */
function resetIdleTimer() {
  if (idleTimer) clearTimeout(idleTimer)
  if (!hardware.kioskMode || !hardware.idleTimeoutSeconds) return
  lastInteraction = Date.now()
  idleTimer = setTimeout(() => {
    if (Date.now() - lastInteraction < hardware.idleTimeoutSeconds) {
      resetIdleTimer()
      return
    }
    if (hardware.returnHome && mainWindow) {
      void mainWindow.loadURL(hardware.serverUrl)
    }
  }, hardware.idleTimeoutSeconds * 1000)
}

function attachInputListeners(window) {
  const bump = () => {
    lastInteraction = Date.now()
    resetIdleTimer()
  }
  for (const event of ['mousedown', 'touchstart', 'keydown', 'wheel']) {
    window.webContents.on('input-event', bump)
  }
  resetIdleTimer()
}

// One instance only: a second launch should focus the existing kiosk, not open
// a second reader on a device meant for one.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(() => {
    const window = createWindow()
    attachInputListeners(window)
    // The renderer asks for the hardware profile so it can size its touch
    // targets and label unavailable peripherals honestly.
    window.webContents.on('did-finish-load', () => {
      window.webContents.send('hardware-profile', hardware)
    })
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin' || hardware.kioskMode) app.quit()
  })

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
}

// A narrow command surface for the renderer, and nothing else. There is
// deliberately no way to reach the filesystem, the database or a shell.
ipcMain.handle('hardware:profile', () => hardware)
