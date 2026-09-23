// Garments AR: the garment page IS the AR page. 8th Wall engine (SLAM) + three.js.
// Flow: camera opens -> scan floor -> reticle -> tap / "Place here" -> garment stands there at a fixed
// scale (− / + to resize, swipe on it to rotate). "View Video" and "View Patterns" open overlays on top
// of the AR view; their close buttons return to the same AR session.
//
// Garment data comes from window.GARMENT (baked in by build_site.py) or, on the shared /ar/?m=<id>
// page, from ../models.json.

import * as THREE from 'three'
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js'
import {DRACOLoader} from 'three/addons/loaders/DRACOLoader.js'
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js'

window.THREE = THREE   // the 8th Wall three.js pipeline module expects the global

const DRACO_PATH = 'https://cdn.jsdelivr.net/npm/three@0.183.2/examples/jsm/libs/draco/gltf/'
const HIT_TYPES = ['DETECTED_SURFACE', 'ESTIMATED_SURFACE', 'FEATURE_POINT']
// Responsive scale: the engine keeps the floor at y = 0 and the camera starts at this height, which fixes
// the scene scale for the whole session. 1.4 m is a typical hand-held phone height when standing.
const CAMERA_HEIGHT = 1.4
const SCALE_MIN = 0.5, SCALE_MAX = 2.0, SCALE_STEP = 0.1
const ROTATE_RAD_PER_PX = 0.012
const FLOOR_SNAP = 0.15        // hits within this height of the tracked floor are snapped onto it (metres)
const POSE_SMOOTHING = 0.55    // 0..1 blend of the new camera pose per frame; lower = smoother but laggier

const $ = (id) => document.getElementById(id)
const ui = {
  hint: $('hint'), controls: $('controls'), place: $('place'), reset: $('reset'),
  sizer: $('sizer'), smaller: $('smaller'), bigger: $('bigger'), scale: $('scale'), title: $('title'),
  videoBtn: $('videoBtn'), patternsBtn: $('patternsBtn'),
  videoBox: $('videoBox'), vid: $('vid'), vclose: $('vclose'), vpp: $('vpp'), vscrub: $('vscrub'), vtime: $('vtime'),
  viewer: $('viewer'), pattern: $('pattern'), pclose: $('pclose'), zoomhint: $('zoomhint'),
}
const setHint = (t, warn = false) => {
  ui.hint.textContent = t
  ui.hint.style.display = t ? '' : 'none'
  ui.hint.classList.toggle('warn', !!warn)
}
const SCAN_HINT = 'Move your phone slowly to scan the floor, then tap where the garment should stand.'
const PLACED_HINT = 'Swipe on the garment to rotate it. Use − / + to change its size.'

// ---- which garment?
let entry = window.GARMENT || null
if (!entry) {
  const modelId = new URLSearchParams(location.search).get('m') || '1'
  const manifest = await fetch('../models.json', {cache: 'no-cache'}).then(r => r.json()).catch(() => null)
  entry = manifest && manifest.models.find(m => String(m.id) === String(modelId))
}
if (!entry) {
  setHint('Unknown garment. Scan the QR code again.')
  throw new Error('garment not found')
}
ui.title.textContent = entry.name
document.title = entry.name + ' · AR'

// ---- overlays: video and patterns (independent of the AR pipeline)
const setupOverlays = () => {
  // video
  if (entry.video) {
    ui.vid.src = '../' + entry.video
  } else {
    ui.videoBtn.hidden = true
  }
  const fmt = (t) => { t = Math.max(0, t || 0); return Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0') }
  let scrubbing = false
  const paint = () => {
    const d = ui.vid.duration || 0, t = ui.vid.currentTime || 0
    if (!scrubbing) ui.vscrub.value = d ? Math.round(t / d * 1000) : 0
    ui.vscrub.style.setProperty('--p', (d ? t / d * 100 : 0) + '%')
    ui.vtime.textContent = fmt(t) + ' / ' + fmt(d)
    ui.vpp.textContent = ui.vid.paused ? '▶' : '❚❚'
  }
  ;['timeupdate', 'durationchange', 'loadedmetadata', 'play', 'pause', 'seeked', 'ended'].forEach(ev => ui.vid.addEventListener(ev, paint))
  ui.vscrub.addEventListener('pointerdown', () => { scrubbing = true })
  ui.vscrub.addEventListener('input', () => { if (ui.vid.duration) ui.vid.currentTime = ui.vscrub.value / 1000 * ui.vid.duration; paint() })
  const endScrub = () => { scrubbing = false }
  ui.vscrub.addEventListener('pointerup', endScrub); ui.vscrub.addEventListener('pointercancel', endScrub); ui.vscrub.addEventListener('change', endScrub)
  const toggle = () => { if (ui.vid.paused) ui.vid.play().catch(() => {}); else ui.vid.pause() }
  ui.vpp.addEventListener('click', toggle)
  ui.vid.addEventListener('click', toggle)
  ui.videoBtn.addEventListener('click', () => {
    ui.videoBox.hidden = false
    if (ui.vid.ended) ui.vid.currentTime = 0
    ui.vid.muted = false
    ui.vid.play().catch(() => {})   // opened from a tap, so sound is allowed
  })
  ui.vclose.addEventListener('click', () => { ui.vid.pause(); ui.videoBox.hidden = true })
  paint()

  // patterns: pinch / wheel / double-tap zoom, drag to pan
  if (entry.pattern) {
    ui.pattern.src = '../' + entry.pattern
  } else {
    ui.patternsBtn.hidden = true
  }
  const viewer = ui.viewer, img = ui.pattern
  let s = 1, tx = 0, ty = 0, fit = 1
  const apply = () => { img.style.transform = `translate(${tx}px, ${ty}px) scale(${s})` }
  const fitToScreen = () => {
    const vw = viewer.clientWidth, vh = viewer.clientHeight
    fit = Math.min(vw / img.naturalWidth, vh / img.naturalHeight)
    s = fit; tx = (vw - img.naturalWidth * s) / 2; ty = (vh - img.naturalHeight * s) / 2; apply()
  }
  const zoomAt = (factor, cx, cy) => {
    const ns = Math.min(Math.max(s * factor, fit), fit * 8)
    const k = ns / s
    tx = cx - (cx - tx) * k; ty = cy - (cy - ty) * k; s = ns; apply()
  }
  ui.patternsBtn.addEventListener('click', () => {
    viewer.hidden = false
    ui.zoomhint.style.opacity = 1; setTimeout(() => { ui.zoomhint.style.opacity = 0 }, 2500)
    if (img.complete && img.naturalWidth) fitToScreen(); else img.onload = fitToScreen
  })
  ui.pclose.addEventListener('click', () => { viewer.hidden = true })
  window.addEventListener('resize', () => { if (!viewer.hidden) fitToScreen() })
  const pts = new Map(); let lastDist = 0, lastMid = null, lastTapT = 0, moved = false
  viewer.addEventListener('pointerdown', (e) => {
    if (e.target === ui.pclose) return
    viewer.setPointerCapture(e.pointerId); pts.set(e.pointerId, {x: e.clientX, y: e.clientY}); moved = false
    if (pts.size === 2) { const [a, b] = [...pts.values()]; lastDist = Math.hypot(a.x - b.x, a.y - b.y); lastMid = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2} }
  })
  viewer.addEventListener('pointermove', (e) => {
    if (!pts.has(e.pointerId)) return
    const prev = pts.get(e.pointerId); pts.set(e.pointerId, {x: e.clientX, y: e.clientY})
    if (pts.size === 1) { tx += e.clientX - prev.x; ty += e.clientY - prev.y; if (Math.hypot(e.clientX - prev.x, e.clientY - prev.y) > 2) moved = true; apply() }
    else if (pts.size === 2) {
      const [a, b] = [...pts.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y); const mid = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}
      if (lastDist > 0) zoomAt(d / lastDist, mid.x, mid.y)
      tx += mid.x - lastMid.x; ty += mid.y - lastMid.y; apply()
      lastDist = d; lastMid = mid; moved = true
    }
  })
  const up = (e) => {
    pts.delete(e.pointerId); lastDist = 0
    if (pts.size === 0 && !moved && e.target !== ui.pclose) {
      const now = Date.now()
      if (now - lastTapT < 320) { s > fit * 1.05 ? fitToScreen() : zoomAt(2.5, e.clientX, e.clientY); lastTapT = 0 } else lastTapT = now
    }
  }
  viewer.addEventListener('pointerup', up); viewer.addEventListener('pointercancel', up)
  viewer.addEventListener('wheel', (e) => { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY) }, {passive: false})
  viewer.addEventListener('touchmove', (e) => e.preventDefault(), {passive: false})   // keep Safari from zooming the page
  document.addEventListener('gesturestart', (e) => { if (!viewer.hidden) e.preventDefault() })
  document.addEventListener('keydown', (e) => { if (e.key !== 'Escape') return; if (!viewer.hidden) viewer.hidden = true; if (!ui.videoBox.hidden) ui.vclose.click() })
}
setupOverlays()

// ---- AR scene pipeline module
const scenePipelineModule = () => {
  let model = null, hitProxy = null, reticle = null, shadowPlane = null
  let placed = false, loadError = null, lastHit = null, scale = 1.0, drag = null
  let smoothPos = null, smoothQuat = null, trackingBad = false

  const loadModel = () => {
    const draco = new DRACOLoader().setDecoderPath(DRACO_PATH)
    const loader = new GLTFLoader().setDRACOLoader(draco)
    return new Promise((resolve, reject) => {
      loader.load('../' + entry.glb, (gltf) => {
        const root = gltf.scene
        root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.frustumCulled = false } })
        const box = new THREE.Box3().setFromObject(root)
        const size = new THREE.Vector3(); box.getSize(size)
        const center = new THREE.Vector3(); box.getCenter(center)
        hitProxy = new THREE.Mesh(
          new THREE.BoxGeometry(Math.max(size.x, 0.5), size.y, Math.max(size.z, 0.5)),
          new THREE.MeshBasicMaterial({colorWrite: false, depthWrite: false}))
        hitProxy.position.copy(center)
        hitProxy.castShadow = false
        root.add(hitProxy)
        resolve(root)
      }, undefined, reject)
    })
  }

  const initXrScene = ({scene, camera, renderer}) => {
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.0
    const pmrem = new THREE.PMREMGenerator(renderer)
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    scene.environmentIntensity = 0.9
    const sun = new THREE.DirectionalLight(0xffffff, 1.6)
    sun.position.set(2, 5, 2); sun.castShadow = true
    sun.shadow.mapSize.set(1024, 1024)
    sun.shadow.camera.near = 0.1; sun.shadow.camera.far = 12
    sun.shadow.camera.left = sun.shadow.camera.bottom = -2
    sun.shadow.camera.right = sun.shadow.camera.top = 2
    sun.shadow.bias = -0.0005
    scene.add(sun)
    scene.add(new THREE.HemisphereLight(0xffffff, 0x666666, 0.6))
    reticle = new THREE.Mesh(
      new THREE.RingGeometry(0.16, 0.2, 48).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({color: 0xff4d6d, transparent: true, opacity: 0.9, depthTest: false}))
    reticle.visible = false
    scene.add(reticle)
    shadowPlane = new THREE.Mesh(
      new THREE.CircleGeometry(1.2, 48).rotateX(-Math.PI / 2),
      new THREE.ShadowMaterial({opacity: 0.45}))
    shadowPlane.receiveShadow = true
    shadowPlane.visible = false
    scene.add(shadowPlane)
    camera.position.set(0, CAMERA_HEIGHT, 0)
    loadModel().then((root) => {
      model = root; model.visible = false; scene.add(model)
      if (!placed) setHint(SCAN_HINT)
    }).catch((e) => {
      loadError = e
      setHint('Could not load the garment model. Check your connection and reload.')
      console.error(e)
    })
  }

  const applyScale = () => {
    if (!model) return
    model.scale.setScalar(scale)
    shadowPlane.scale.setScalar(scale)
    ui.scale.textContent = Math.round(scale * 100) + '%'
    ui.smaller.disabled = scale <= SCALE_MIN + 1e-6
    ui.bigger.disabled = scale >= SCALE_MAX - 1e-6
  }

  const place = (hit) => {
    if (!model || !hit) return
    const {camera} = XR8.Threejs.xrScene()
    // the engine keeps the tracked floor at y = 0; anchoring there means its refinements never lift or sink the model
    const y = Math.abs(hit.position.y) < FLOOR_SNAP ? 0 : hit.position.y
    model.position.set(hit.position.x, y, hit.position.z)
    const dx = camera.position.x - hit.position.x, dz = camera.position.z - hit.position.z
    model.rotation.set(0, Math.atan2(dx, dz), 0)
    model.visible = true
    shadowPlane.position.copy(model.position); shadowPlane.visible = true
    reticle.visible = false
    placed = true
    applyScale()
    setHint(PLACED_HINT)
    ui.place.style.display = 'none'
    ui.sizer.classList.add('on')
  }

  const reset = () => {
    placed = false; drag = null
    if (model) model.visible = false
    shadowPlane.visible = false
    ui.place.style.display = ''
    ui.sizer.classList.remove('on')
    setHint(SCAN_HINT)
  }

  const raycaster = new THREE.Raycaster()
  const floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
  const rayFrom = (nx, ny) => {
    const {camera} = XR8.Threejs.xrScene()
    raycaster.setFromCamera(new THREE.Vector2(nx * 2 - 1, -(ny * 2 - 1)), camera)
    return camera
  }
  const floorHit = (nx, ny) => {
    const camera = rayFrom(nx, ny)
    const p = new THREE.Vector3()
    if (!raycaster.ray.intersectPlane(floorPlane, p)) return null
    const d = p.distanceTo(camera.position)
    if (d < 0.4 || d > 8) return null
    return {type: 'FLOOR_PLANE', position: {x: p.x, y: p.y, z: p.z}, distance: d}
  }
  const hitAt = (nx, ny) => {
    const hits = XR8.XrController.hitTest(nx, ny, HIT_TYPES) || []
    for (const t of HIT_TYPES) { const h = hits.find(x => x.type === t); if (h) return h }
    return hits[0] || floorHit(nx, ny)
  }
  const touchesModel = (nx, ny) => {
    if (!model || !hitProxy || !model.visible) return false
    rayFrom(nx, ny)
    return raycaster.intersectObject(hitProxy, false).length > 0
  }

  return {
    name: 'garments-scene',
    onStart: ({canvas}) => {
      const {scene, camera, renderer} = XR8.Threejs.xrScene()
      initXrScene({scene, camera, renderer})
      XR8.XrController.updateCameraProjectionMatrix({origin: camera.position, facing: camera.quaternion})
      canvas.addEventListener('touchmove', (e) => e.preventDefault(), {passive: false})
      canvas.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) { drag = null; return }
        const t = e.touches[0]
        const nx = t.clientX / window.innerWidth, ny = t.clientY / window.innerHeight
        drag = (placed && touchesModel(nx, ny)) ? {startX: t.clientX, startRot: model.rotation.y, moved: false} : null
      }, {passive: true})
      canvas.addEventListener('touchmove', (e) => {
        if (!drag || e.touches.length !== 1) return
        const dxp = e.touches[0].clientX - drag.startX
        if (Math.abs(dxp) > 4) drag.moved = true
        if (drag.moved) model.rotation.y = drag.startRot + dxp * ROTATE_RAD_PER_PX
      }, {passive: true})
      canvas.addEventListener('touchend', (e) => {
        const wasDrag = drag && drag.moved
        drag = null
        if (wasDrag || placed || e.changedTouches.length !== 1) return
        const t = e.changedTouches[0]
        const hit = hitAt(t.clientX / window.innerWidth, t.clientY / window.innerHeight) || lastHit
        if (hit) place(hit)
        else setHint('No floor found there yet. Point at the floor and move a little, then tap again.')
      }, {passive: true})
      canvas.addEventListener('touchcancel', () => { drag = null }, {passive: true})
      ui.place.addEventListener('click', () => { if (lastHit) place(lastHit) })
      ui.reset.addEventListener('click', reset)
      ui.smaller.addEventListener('click', () => { scale = Math.max(SCALE_MIN, +(scale - SCALE_STEP).toFixed(2)); applyScale() })
      ui.bigger.addEventListener('click', () => { scale = Math.min(SCALE_MAX, +(scale + SCALE_STEP).toFixed(2)); applyScale() })
      ui.controls.classList.add('on')
      setHint(model ? 'Tap where the garment should stand.' : 'Loading the garment…')
    },
    listeners: [{
      event: 'reality.trackingstatus',
      process: ({detail}) => {
        const bad = detail && detail.status && detail.status !== 'NORMAL'
        if (bad === trackingBad) return
        trackingBad = bad
        if (!placed) return
        if (bad) setHint('Tracking is limited. Move the phone slowly and keep the floor in view.', true)
        else setHint(PLACED_HINT)
      },
    }],
    onUpdate: () => {
      if (POSE_SMOOTHING < 1) {
        const {camera} = XR8.Threejs.xrScene()
        if (!smoothPos) {
          smoothPos = camera.position.clone(); smoothQuat = camera.quaternion.clone()
        } else {
          smoothPos.lerp(camera.position, POSE_SMOOTHING)
          smoothQuat.slerp(camera.quaternion, POSE_SMOOTHING)
          camera.position.copy(smoothPos)
          camera.quaternion.copy(smoothQuat)
        }
      }
      if (placed || !reticle) return
      const hit = hitAt(0.5, 0.62)
      if (hit) {
        lastHit = hit
        reticle.position.set(hit.position.x, hit.position.y, hit.position.z)
        reticle.visible = !!model
        if (model && !loadError) setHint('Tap to place the garment on the floor.')
      } else {
        reticle.visible = false
        if (model && !loadError) setHint('Move your phone slowly to scan the floor.')
      }
    },
  }
}

const onxrloaded = () => {
  XR8.XrController.configure({scale: 'responsive', enableLighting: false})
  XR8.addCameraPipelineModules([
    XR8.GlTextureRenderer.pipelineModule(),
    XR8.Threejs.pipelineModule(),
    XR8.XrController.pipelineModule(),
    LandingPage.pipelineModule(),
    XRExtras.FullWindowCanvas.pipelineModule(),
    XRExtras.Loading.pipelineModule(),
    XRExtras.RuntimeError.pipelineModule(),
    scenePipelineModule(),
  ])
  XR8.run({canvas: document.getElementById('camerafeed')})
}
window.XR8 ? onxrloaded() : window.addEventListener('xrloaded', onxrloaded)
