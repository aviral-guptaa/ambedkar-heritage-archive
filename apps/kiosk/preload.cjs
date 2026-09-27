/**
 * The only bridge between the kiosk shell and the archive.
 *
 * It exposes the hardware profile and nothing else. The renderer cannot reach
 * the filesystem, a shell command or the database, because the kiosk displays a
 * third-party corpus that should never be able to run anything on the machine
 * it is being read on.
 */
const { contextBridge, ipcRenderer } = require('electron')

const ALLOWED_EVENTS = new Set(['hardware-profile'])

contextBridge.exposeInMainWorld('kiosk', {
  /** What this machine can actually do, so the interface can say so too. */
  profile: () => ipcRenderer.invoke('hardware:profile'),

  /**
   * Listen for a push from the shell. The event name is checked against a fixed
   * list so a compromised page cannot subscribe to arbitrary channels.
   */
  on: (event, handler) => {
    if (!ALLOWED_EVENTS.has(event)) {
      throw new Error(`Refusing to subscribe to an unknown channel: ${event}`)
    }
    const wrapped = (_event, payload) => handler(payload)
    ipcRenderer.on(event, wrapped)
    return () => ipcRenderer.removeListener(event, wrapped)
  },
})
