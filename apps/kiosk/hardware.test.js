/**
 * The kiosk's rules, tested without a display.
 *
 * Every assertion here is about a way a public machine could behave wrongly:
 * a window a visitor can escape from, a peripheral the machine does not have, a
 * frame large enough to look like the kiosk works when it is not.
 */
import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'

import {
  flag,
  isExternalLink,
  isInternalUrl,
  permissionDecision,
  positiveNumber,
  readProfile,
  windowOptions,
} from './hardware.js'

describe('flag', () => {
  it('accepts the usual ways of writing yes', () => {
    for (const value of ['1', 'true', 'TRUE', 'yes', 'on', 'On']) {
      assert.equal(flag({ X: value }, 'X'), true, `${value} should be true`)
    }
  })

  it('treats anything else as off', () => {
    for (const value of ['0', 'false', 'no', 'off', 'maybe', '2', 'enabled']) {
      assert.equal(flag({ X: value }, 'X', true), false, `${value} should be false`)
    }
  })

  it('reads a variable that is present but empty as not configured', () => {
    // `HW_CAMERA_ENABLED=` in a shell or a compose file means the same as not
    // writing it, so it takes the default rather than inventing an answer.
    assert.equal(flag({ X: '' }, 'X', false), false)
    assert.equal(flag({ X: '  ' }, 'X', false), false)
    assert.equal(flag({ X: '' }, 'X', true), true)
  })

  it('is not fooled by surrounding whitespace', () => {
    assert.equal(flag({ X: ' true ' }, 'X'), true)
    assert.equal(flag({ X: ' TRUE ' }, 'X'), true)
  })

  it('uses the fallback when the variable is absent', () => {
    assert.equal(flag({}, 'MISSING'), false)
    assert.equal(flag({}, 'MISSING', true), true)
  })
})

describe('positiveNumber', () => {
  it('reads a positive number', () => {
    assert.equal(positiveNumber({ N: '56' }, 'N', 44), 56)
    assert.equal(positiveNumber({ N: '7.5' }, 'N', 44), 7.5)
  })

  it('falls back for values that would be nonsense', () => {
    for (const value of ['0', '-1', 'abc', '', undefined, Number.NaN]) {
      assert.equal(positiveNumber({ N: value }, 'N', 44), 44, `${value} should fall back`)
    }
  })
})

describe('readProfile', () => {
  it('assumes no optional peripherals', () => {
    const profile = readProfile({})
    assert.equal(profile.scannerEnabled, false)
    assert.equal(profile.microphoneEnabled, false)
    assert.equal(profile.cameraEnabled, false)
    // Reading passages aloud is the one capability the archive expects to work.
    assert.equal(profile.speakerEnabled, true)
  })

  it('reads the same HW_ names the API uses', () => {
    const profile = readProfile({
      HW_KIOSK_MODE: 'true',
      HW_SCANNER_ENABLED: 'yes',
      HW_TOUCH_MIN_TARGET_PX: '56',
      HW_KIOSK_IDLE_TIMEOUT_SECONDS: '60',
    })
    assert.equal(profile.kioskMode, true)
    assert.equal(profile.scannerEnabled, true)
    assert.equal(profile.touchMinTargetPx, 56)
    assert.equal(profile.idleTimeoutSeconds, 60)
  })
})

const PRELOAD = '/opt/kiosk/preload.cjs'

describe('windowOptions', () => {
  const display = { width: 1920, height: 1080 }

  it('fills the display with no frame when it is a kiosk', () => {
    const options = windowOptions(readProfile({ HW_KIOSK_MODE: 'true' }), display, PRELOAD)
    assert.equal(options.fullscreen, true)
    assert.equal(options.frame, false)
    assert.equal(options.kiosk, true)
    assert.equal(options.width, 1920)
    assert.equal(options.height, 1080)
  })

  it('keeps a frame and stays inside the display otherwise', () => {
    const options = windowOptions(readProfile({}), display, PRELOAD)
    assert.equal(options.frame, true)
    assert.equal(options.kiosk, false)
    assert.equal(options.fullscreen, false)
    assert.ok(options.width <= display.width)
    assert.ok(options.height <= display.height)
  })

  it('never opens larger than the display it was given', () => {
    const options = windowOptions(readProfile({}), { width: 800, height: 600 }, PRELOAD)
    assert.equal(options.width, 800)
    assert.equal(options.height, 600)
  })

  it('refuses a relative preload path', () => {
    // Electron silently disables the bridge for a relative path, so the page
    // would find no hardware profile and quietly behave as a plain website.
    assert.throws(() => windowOptions(readProfile({}), display, 'preload.cjs'), /absolute/)
    assert.throws(() => windowOptions(readProfile({}), display, undefined), /must be supplied/)
  })

  it('cannot be made to expose the renderer to the machine', () => {
    // These are not configurable by a hardware profile, because a setting that
    // could turn them off would be a way to turn them off.
    for (const profile of [readProfile({}), readProfile({ HW_KIOSK_MODE: 'true' })]) {
      const prefs = windowOptions(profile, display, PRELOAD).webPreferences
      assert.equal(prefs.contextIsolation, true)
      assert.equal(prefs.nodeIntegration, false)
      assert.equal(prefs.sandbox, true)
    }
  })
})

describe('permissionDecision', () => {
  it('refuses the microphone unless the hardware was told it exists', () => {
    assert.equal(permissionDecision('media', readProfile({})), false)
    assert.equal(permissionDecision('audioCapture', readProfile({})), false)
    assert.equal(permissionDecision('media', readProfile({ HW_MICROPHONE_ENABLED: 'true' })), true)
  })

  it('refuses the camera unless the hardware was told it exists', () => {
    assert.equal(permissionDecision('videoCapture', readProfile({})), false)
    assert.equal(
      permissionDecision('videoCapture', readProfile({ HW_CAMERA_ENABLED: 'true' })),
      true,
    )
  })

  it('refuses a permission it has never heard of', () => {
    // A browser asking for something unexpected gets no, rather than a silent
    // default of yes.
    assert.equal(permissionDecision('geolocation', readProfile({})), false)
    assert.equal(permissionDecision('midi', readProfile({ HW_CAMERA_ENABLED: 'true' })), false)
  })
})

describe('isInternalUrl', () => {
  const profile = readProfile({ HARDWARE_SERVER_URL: 'http://localhost:4173' })

  it('allows the archive itself', () => {
    assert.equal(isInternalUrl('http://localhost:4173/manuscripts', profile), true)
    assert.equal(isInternalUrl('http://localhost:4173/', profile), true)
  })

  it('refuses anywhere else, including a look-alike host', () => {
    assert.equal(isInternalUrl('https://example.com/', profile), false)
    assert.equal(isInternalUrl('http://localhost:4174/', profile), false)
    // A host that merely starts with the same characters must not be accepted.
    assert.equal(isInternalUrl('http://localhost:4173.evil.test/', profile), false)
    assert.equal(isInternalUrl('javascript:alert(1)', profile), false)
    assert.equal(isInternalUrl('not a url', profile), false)
  })
})

describe('isExternalLink', () => {
  it('recognises a web link a reader may want to follow', () => {
    assert.equal(isExternalLink('https://www.constitutionofindia.net/'), true)
    assert.equal(isExternalLink('http://example.com/'), true)
  })

  it('does not treat a local path or a dangerous scheme as a link to open', () => {
    assert.equal(isExternalLink('/manuscripts'), false)
    assert.equal(isExternalLink('javascript:alert(1)'), false)
    assert.equal(isExternalLink('file:///etc/passwd'), false)
  })
})
