/**
 * Reading the kiosk's hardware profile from the page.
 *
 * When the archive is shown in the kiosk shell, the shell tells it what the
 * machine can do. When it is shown in a browser, there is no shell, and the
 * interface must behave as an ordinary website rather than assuming a scanner
 * or a touchscreen exists.
 */

export interface HardwareProfile {
  kioskMode: boolean
  scannerEnabled: boolean
  microphoneEnabled: boolean
  speakerEnabled: boolean
  cameraEnabled: boolean
  touchMinTargetPx: number
  idleTimeoutSeconds: number
  returnHome: boolean
  serverUrl: string
  profile: string
}

/** What the interface assumes before the shell has said anything. */
export const NO_HARDWARE: HardwareProfile = {
  kioskMode: false,
  scannerEnabled: false,
  microphoneEnabled: false,
  speakerEnabled: true,
  cameraEnabled: false,
  touchMinTargetPx: 44,
  idleTimeoutSeconds: 180,
  returnHome: true,
  serverUrl: '',
  profile: 'browser',
}

interface KioskBridge {
  profile(): Promise<HardwareProfile>
  on(event: 'hardware-profile', handler: (profile: HardwareProfile) => void): () => void
}

declare global {
  interface Window {
    kiosk?: KioskBridge
  }
}

export function isKiosk(): boolean {
  return typeof window !== 'undefined' && Boolean(window.kiosk)
}

/**
 * Ask the shell what this machine can do.
 *
 * Returns the plain-browser defaults when there is no shell, so a component can
 * read one value instead of branching on the environment everywhere.
 */
export async function readHardware(): Promise<HardwareProfile> {
  if (!isKiosk()) return NO_HARDWARE
  try {
    const profile = await window.kiosk!.profile()
    return { ...NO_HARDWARE, ...profile }
  } catch {
    // A shell that will not answer is treated as a plain browser, which is the
    // safe direction: fewer capabilities offered, not more.
    return NO_HARDWARE
  }
}
