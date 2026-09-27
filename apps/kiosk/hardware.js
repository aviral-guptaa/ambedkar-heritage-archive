/**
 * Hardware profile and window geometry, with no Electron imports.
 *
 * Kept separate from main.js so the rules can be tested on a machine with no
 * display — which is the situation this project is usually built in. The
 * decisions that matter for a kiosk are all here: what counts as enabled, how
 * large a touch target has to be, and whether the frame is hidden.
 */

/**
 * Read a capability flag.
 *
 * Defaults to false when unset. Assuming a scanner or a microphone is present
 * would put a button on screen that cannot work, which is worse than not
 * offering it.
 */
export function flag(env, name, fallback = false) {
  const raw = env[name]
  if (raw === undefined || raw === null) return fallback
  const value = String(raw).trim()
  // A variable that is present but empty means "not configured", the same as an
  // absent one — otherwise `HW_CAMERA_ENABLED=` would silently differ from not
  // writing the line at all.
  if (value === '') return fallback
  return /^(1|true|yes|on)$/i.test(value)
}

/** Read a positive number, falling back when the value is absent or nonsense. */
export function positiveNumber(env, name, fallback) {
  const value = Number(env[name])
  return Number.isFinite(value) && value > 0 ? value : fallback
}

/**
 * Build the profile from an environment.
 *
 * The names match the HW_ settings the API already reads, so one set of
 * variables configures both the shell and the server.
 */
export function readProfile(env = {}) {
  return {
    kioskMode: flag(env, 'HW_KIOSK_MODE', false),
    scannerEnabled: flag(env, 'HW_SCANNER_ENABLED', false),
    microphoneEnabled: flag(env, 'HW_MICROPHONE_ENABLED', false),
    speakerEnabled: flag(env, 'HW_SPEAKER_ENABLED', true),
    cameraEnabled: flag(env, 'HW_CAMERA_ENABLED', false),
    touchMinTargetPx: positiveNumber(env, 'HW_TOUCH_MIN_TARGET_PX', 44),
    idleTimeoutSeconds: positiveNumber(env, 'HW_KIOSK_IDLE_TIMEOUT_SECONDS', 180),
    returnHome: flag(env, 'HW_KIOSK_RETURN_HOME', true),
    serverUrl: env.HARDWARE_SERVER_URL ?? 'http://localhost:4173',
    profile: env.HARDWARE_PROFILE ?? 'default',
  }
}

/**
 * The window to open.
 *
 * A kiosk is fullscreen and frameless, because it is unattended public
 * hardware. Anything else keeps a frame and stays inside the display, because a
 * development build you cannot drag or inspect is a development build you
 * cannot debug.
 */
export function windowOptions(profile, display, preloadPath) {
  const base = {
    width: Math.min(profile.kioskMode ? display.width : 1280, display.width),
    height: Math.min(profile.kioskMode ? display.height : 860, display.height),
    backgroundColor: '#f5f5f4',
  }
  if (!profile.kioskMode) {
    return {
      ...base,
      frame: true,
      fullscreen: false,
      kiosk: false,
      webPreferences: preferences(preloadPath),
    }
  }
  return {
    ...base,
    fullscreen: true,
    frame: false,
    kiosk: true,
    autoHideMenuBar: true,
    thickFrame: false,
    webPreferences: preferences(preloadPath),
  }
}

/**
 * Renderer settings.
 *
 * The kiosk displays a third-party corpus, so the renderer is sandboxed, has no
 * Node integration, and can only be reached through the preload bridge. These
 * are not adjustable by a hardware profile on purpose: a setting that could turn
 * them off would be a way to turn them off.
 */
function preferences(preloadPath) {
  return {
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    // Electron silently drops a relative preload and the page then finds no
    // bridge at all, so this is asserted to be absolute where it is built.
    preload: require$absolute(preloadPath),
  }
}

/** Resolve the preload script, refusing a relative path. */
function require$absolute(preloadPath) {
  if (!preloadPath) {
    throw new Error('The kiosk preload script path must be supplied to windowOptions().')
  }
  if (!preloadPath.startsWith('/')) {
    throw new Error(`The kiosk preload script path must be absolute, got: ${preloadPath}`)
  }
  return preloadPath
}

/**
 * Which browser permissions to grant.
 *
 * Unknown permissions are refused rather than allowed, so a browser that asks
 * for something the hardware was never configured with does not get it.
 */
export function permissionDecision(permission, profile) {
  const allowed = {
    media: profile.microphoneEnabled,
    audioCapture: profile.microphoneEnabled,
    videoCapture: profile.cameraEnabled,
    'speaker-selection': profile.speakerEnabled,
  }
  return Boolean(allowed[permission])
}

/**
 * Whether a URL may be loaded inside the kiosk.
 *
 * Only the archive itself. Anything else is a source a reader may want to check,
 * which belongs in their own browser — a kiosk must not be navigated off the
 * archive by a link in a record.
 */
export function isInternalUrl(url, profile) {
  try {
    return new URL(url).origin === new URL(profile.serverUrl).origin
  } catch {
    return false
  }
}

/** Whether a URL is something a reader should be offered in their own browser. */
export function isExternalLink(url) {
  return /^https?:/i.test(url)
}
