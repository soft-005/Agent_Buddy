// 3D avatar: loads a VRM model with three-vrm and animates idle motion,
// blinking, mouth movement while speaking, a mood per sentence, and eye contact.
// The main process picks the model file (any .vrm in assets/models); load()
// runs again when the user switches avatars. If there is no model, or it fails
// to load, a placeholder sphere is shown instead.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

// When docked, this fraction of the window's height still shows the top of the head.
const PEEK_FRACTION = 0.3;

// Moods arrive with each sentence of a reply. A model with the matching VRM
// expression preset shows it; every model also gets the body language below,
// because many models (ones made in Blender, for example) have no mood
// expressions at all, only mouth shapes and a blink.
//   pitch: head down (+) or up (-), radians   tilt: head tilt to the side
//   sway: size of the idle sway   pace: speed of the idle motion
//   lids: eyelids part closed, 0-1   wide: eyes wide open, fewer blinks, 0-1
const MOOD_POSES = {
  neutral: { pitch: 0, tilt: 0, sway: 1, pace: 1, lids: 0, wide: 0 },
  happy: { pitch: -0.06, tilt: 0.06, sway: 1.5, pace: 1.5, lids: 0, wide: 0 },
  sad: { pitch: 0.18, tilt: 0.08, sway: 0.5, pace: 0.6, lids: 0.35, wide: 0 },
  angry: { pitch: 0.1, tilt: 0, sway: 0.6, pace: 1.3, lids: 0.3, wide: 0 },
  surprised: { pitch: -0.1, tilt: 0, sway: 0.7, pace: 1, lids: 0, wide: 1 },
  relaxed: { pitch: 0.02, tilt: 0.12, sway: 0.8, pace: 0.7, lids: 0.3, wide: 0 },
};
const MOODS = Object.keys(MOOD_POSES);
const MOOD_EXPRESSION = 0.7; // weight of a mood's expression preset
const MOOD_LINGER = 6; // seconds a mood stays on the face after Buddy stops speaking

// Eye contact: the avatar looks at you (the camera), or at the mouse pointer
// while it moves. LOOK_DISTANCE is how far behind the screen, in pixels, the
// avatar imagines you: a smaller value turns the head more for the same move.
const LOOK_DISTANCE = 900;
const POINTER_IDLE = 4; // seconds without pointer movement before looking back at you
const HEAD_SHARE = 0.6; // how much of a look the head turns (eyes do the rest, if the model has eye bones)
const MAX_LOOK_YAW = 0.9; // radians, left/right
const MAX_LOOK_PITCH = 0.5; // radians, up/down

export class Avatar {
  // options.visibleFraction: how much of the model's height to fit in view
  // (0.6 = head and upper body on the desktop; the phone app shows more).
  constructor(canvas, options = {}) {
    this.visibleFraction = options.visibleFraction ?? 0.6;
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.renderer.setClearColor(0x000000, 0);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
    this.camera.position.set(0, 1.35, 1.6);
    this.camera.lookAt(0, 1.3, 0);

    const light = new THREE.DirectionalLight(0xffffff, Math.PI);
    light.position.set(1, 1, 1);
    this.scene.add(light, new THREE.AmbientLight(0xffffff, 0.6));

    this.clock = new THREE.Clock();
    this.vrm = null;
    this.placeholder = null;
    this.loadToken = 0; // a newer load() makes an older one give up
    this.axis = 1; // -1 for VRM 0.x models, see load()
    this.speaking = false;
    this.state = "idle"; // idle | listening | thinking | speaking
    this.nextBlink = 2;
    this.phase = 0; // idle motion clock; runs faster or slower with the mood

    this.mood = "neutral";
    this.moodHold = 0;
    this.moodLevels = Object.fromEntries(MOODS.map((m) => [m, m === "neutral" ? 1 : 0]));
    this.presets = new Set(); // mood expressions the current model has

    this.pointer = null; // {x, y} in window pixels, or null to look at you
    this.pointerAt = -Infinity;
    this.look = { yaw: 0, pitch: 0 }; // smoothed look direction, radians
    this.saccade = { yaw: 0, pitch: 0, next: 1 }; // small random glances
    this.lookTarget = new THREE.Object3D();
    this.scene.add(this.lookTarget);

    // Docking: the model slides down by dockOffset (world units) until only
    // the top of its head shows at the bottom of the window.
    this.docked = false;
    this.dockOffset = 0;
    this.dockTarget = 0;
    this.peekOffset = this.computePeek(1.48, 1.3, 1.6); // placeholder framing; models recompute

    this.resize();
    window.addEventListener("resize", () => this.resize());
    this.load();
    this.renderer.setAnimationLoop(() => this.tick());
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.renderer.domElement;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // Loads the model the app picked. Called again after the user switches avatars;
  // the old model stays on screen until the new one is ready.
  async load() {
    const token = ++this.loadToken;
    const url = await window.buddy.getAvatarUrl();
    if (token !== this.loadToken) return;
    if (!url) {
      console.warn("No .vrm file in desktop/assets/models; showing placeholder.");
      this.showPlaceholder();
      return;
    }

    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    let gltf;
    try {
      gltf = await loader.loadAsync(url);
    } catch (error) {
      if (token !== this.loadToken) return;
      console.error(`Could not load avatar ${url}:`, error?.message ?? error);
      this.showPlaceholder();
      return;
    }
    const vrm = gltf.userData.vrm;
    if (token !== this.loadToken) {
      VRMUtils.deepDispose(gltf.scene); // the user already picked another one
      return;
    }
    if (!vrm) {
      console.error(`${url} loaded but is not a VRM model; showing placeholder.`);
      this.showPlaceholder();
      return;
    }

    this.clearModel();
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.rotateVRM0(vrm); // VRM 0.x models face the other way
    // three-vrm's normalized bones turn the opposite way around x and z on VRM 0.x
    // models, so poses here are written for VRM 1.0 and flipped with this sign.
    this.axis = vrm.meta?.metaVersion === "0" ? -1 : 1;
    this.scene.add(vrm.scene);
    this.vrm = vrm;
    this.presets = new Set(MOODS.filter((m) => vrm.expressionManager?.getExpression(m)));
    if (vrm.lookAt) vrm.lookAt.target = this.lookTarget;
    this.relaxArms();
    this.frameModel();
    console.info(`Loaded avatar ${decodeURI(url.split("/").pop())} (VRM ${vrm.meta?.metaVersion ?? "?"})`);
  }

  clearModel() {
    if (this.vrm) {
      this.scene.remove(this.vrm.scene);
      VRMUtils.deepDispose(this.vrm.scene);
      this.vrm = null;
    }
    if (this.placeholder) {
      this.scene.remove(this.placeholder);
      this.placeholder.geometry.dispose();
      this.placeholder.material.dispose();
      this.placeholder = null;
    }
  }

  // Fit the top part of the model (head and upper body) in view, whatever its
  // height or head size. Uses height only: the bind-pose box includes T-pose arms.
  frameModel(visibleFraction = this.visibleFraction) {
    this.vrm.scene.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(this.vrm.scene);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());

    const viewHeight = size.y * visibleFraction;
    const centerY = box.max.y - viewHeight / 2;
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const distance = (viewHeight / 2 / Math.tan(halfFov)) * 1.15; // 15% margin

    this.camera.position.set(center.x, centerY, center.z + distance);
    this.camera.lookAt(center.x, centerY, center.z);
    this.peekOffset = this.computePeek(box.max.y, centerY, distance);
    if (this.docked) this.dockTarget = this.peekOffset;
  }

  // How far to lower the model so its top sits PEEK_FRACTION above the view's bottom.
  computePeek(topY, centerY, distance) {
    const halfHeight = distance * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    const viewBottom = centerY - halfHeight;
    return viewBottom + PEEK_FRACTION * 2 * halfHeight - topY;
  }

  setDocked(docked) {
    this.docked = docked;
    this.dockTarget = docked ? this.peekOffset : 0;
  }

  relaxArms() {
    const h = this.vrm.humanoid;
    const l = h.getNormalizedBoneNode("leftUpperArm");
    const r = h.getNormalizedBoneNode("rightUpperArm");
    if (l) l.rotation.z = -1.2 * this.axis;
    if (r) r.rotation.z = 1.2 * this.axis;
  }

  showPlaceholder() {
    this.clearModel();
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(0.18, 48, 48),
      new THREE.MeshStandardMaterial({ color: 0x7aa2ff, roughness: 0.4 })
    );
    mesh.position.set(0, 1.3, 0);
    this.scene.add(mesh);
    this.placeholder = mesh;
  }

  setState(state) {
    this.state = state;
    this.speaking = state === "speaking";
  }

  // "happy", "sad", "angry", "surprised", "relaxed" or "neutral". It stays while
  // Buddy speaks and for MOOD_LINGER seconds after, then fades back to neutral.
  setMood(mood) {
    this.mood = MOODS.includes(mood) ? mood : "neutral";
    this.moodHold = MOOD_LINGER;
  }

  // Follow the mouse pointer (x, y in this window's pixels; it may be outside it).
  lookAtPointer(x, y) {
    this.pointer = { x, y };
    this.pointerAt = this.clock.elapsedTime;
  }

  // Look at you (the camera) instead of the pointer.
  lookAtViewer() {
    this.pointer = null;
  }

  // Where the head is on screen, in window pixels.
  headOnScreen() {
    const head = this.vrm?.humanoid.getNormalizedBoneNode("head");
    if (!head) return null;
    const p = head.getWorldPosition(new THREE.Vector3()).project(this.camera);
    const { clientWidth: w, clientHeight: h } = this.renderer.domElement;
    return { x: ((p.x + 1) / 2) * w, y: ((1 - p.y) / 2) * h };
  }

  // Smoothly turn toward the pointer, or toward you when the pointer is still.
  updateLook(t, dt) {
    let yaw = 0;
    let pitch = 0;
    const head = this.pointer && t - this.pointerAt < POINTER_IDLE ? this.headOnScreen() : null;
    if (head) {
      yaw = Math.atan2(this.pointer.x - head.x, LOOK_DISTANCE);
      pitch = Math.atan2(this.pointer.y - head.y, LOOK_DISTANCE);
    } else {
      // Small glances now and then look more alive than a fixed stare.
      this.saccade.next -= dt;
      if (this.saccade.next < 0) {
        this.saccade.next = 1.5 + Math.random() * 3;
        const away = Math.random() < 0.6;
        this.saccade.yaw = away ? (Math.random() - 0.5) * 0.12 : 0;
        this.saccade.pitch = away ? (Math.random() - 0.5) * 0.06 : 0;
      }
      yaw = this.saccade.yaw;
      pitch = this.saccade.pitch;
    }
    yaw = THREE.MathUtils.clamp(yaw, -MAX_LOOK_YAW, MAX_LOOK_YAW);
    pitch = THREE.MathUtils.clamp(pitch, -MAX_LOOK_PITCH, MAX_LOOK_PITCH);
    const ease = Math.min(1, dt * 6);
    this.look.yaw += (yaw - this.look.yaw) * ease;
    this.look.pitch += (pitch - this.look.pitch) * ease;

    // The eyes aim at a point in front of the camera, offset toward the pointer.
    const c = this.camera.position;
    const distance = Math.max(0.3, c.z - (this.vrm?.scene.position.z ?? 0));
    this.lookTarget.position.set(
      c.x + Math.tan(this.look.yaw) * distance,
      c.y - Math.tan(this.look.pitch) * distance,
      c.z
    );
  }

  // Blend the mood poses by how far each mood has faded in.
  updateMood(dt) {
    if (this.state !== "speaking") {
      this.moodHold -= dt;
      if (this.moodHold <= 0) this.mood = "neutral";
    }
    const ease = Math.min(1, dt * 3);
    const pose = { pitch: 0, tilt: 0, sway: 0, pace: 0, lids: 0, wide: 0 };
    for (const m of MOODS) {
      this.moodLevels[m] += ((m === this.mood ? 1 : 0) - this.moodLevels[m]) * ease;
      for (const key in pose) pose[key] += MOOD_POSES[m][key] * this.moodLevels[m];
    }
    return pose;
  }

  tick() {
    const dt = this.clock.getDelta();
    const t = this.clock.elapsedTime;

    // Slide toward the docked or awake position (eased).
    this.dockOffset += (this.dockTarget - this.dockOffset) * Math.min(1, dt * 5);
    const pose = this.updateMood(dt);
    this.phase += dt * pose.pace;
    const p = this.phase;

    if (this.vrm) {
      const em = this.vrm.expressionManager;
      const h = this.vrm.humanoid;
      const s = this.axis;
      this.vrm.scene.position.y = this.dockOffset;
      this.updateLook(t, dt);
      const lookYaw = this.look.yaw * HEAD_SHARE;
      const lookPitch = this.look.pitch * HEAD_SHARE;

      // Idle breathing / sway
      const spine = h.getNormalizedBoneNode("spine");
      if (spine) spine.rotation.z = Math.sin(p * 0.8) * 0.02 * pose.sway;
      const neck = h.getNormalizedBoneNode("neck");
      const head = h.getNormalizedBoneNode("head");
      // The neck takes part of the turn so a big look doesn't bend at one joint.
      const neckShare = neck ? 0.4 : 0;
      if (neck) {
        neck.rotation.y = lookYaw * neckShare;
        neck.rotation.x = s * lookPitch * neckShare;
      }
      if (head) {
        const yaw = lookYaw * (1 - neckShare);
        const pitch = lookPitch * (1 - neckShare);
        if (this.docked) {
          // Peeking: a slow curious tilt, glancing around unless the pointer moves.
          const following = this.pointer && t - this.pointerAt < POINTER_IDLE;
          head.rotation.z = Math.sin(t * 1.1) * 0.15;
          head.rotation.y = following ? yaw : Math.sin(t * 0.6) * 0.25;
          head.rotation.x = s * (0.05 + pitch);
        } else {
          const nod = this.state === "thinking" ? 0.12 : Math.sin(p * 0.7) * 0.03 * pose.sway;
          head.rotation.z = s * pose.tilt;
          head.rotation.y = Math.sin(p * 0.5) * 0.08 * pose.sway + yaw;
          head.rotation.x = s * (nod + pose.pitch + pitch);
        }
      }

      if (em) {
        // Blink (less often with wide eyes), never fully open when the mood lowers the lids.
        this.nextBlink -= dt;
        let blink = 0;
        if (this.nextBlink < 0.15) blink = 1 - Math.abs(this.nextBlink) / 0.15;
        if (this.nextBlink < -0.15) this.nextBlink = (2 + Math.random() * 3) * (1 + pose.wide * 2);
        em.setValue("blink", Math.max(0, blink, pose.lids));

        // Mouth: fake lip sync while speaking (replace with visemes from TTS later)
        const mouth = this.speaking ? (Math.sin(t * 18) * 0.5 + 0.5) * 0.7 : 0;
        em.setValue("aa", mouth);
        // A surprised model without a "surprised" expression opens its mouth a little.
        if (!this.presets.has("surprised")) em.setValue("oh", this.speaking ? 0 : this.moodLevels.surprised * 0.3);

        for (const m of this.presets) {
          if (m === "neutral") continue;
          let weight = this.moodLevels[m] * MOOD_EXPRESSION;
          // A light smile while speaking and a curious look while listening, when no mood says otherwise.
          if (m === "happy" && this.speaking) weight = Math.max(weight, 0.3 * this.moodLevels.neutral);
          if (m === "surprised" && this.state === "listening") weight = Math.max(weight, 0.25);
          em.setValue(m, weight);
        }
      }
      this.vrm.update(dt);
    } else if (this.placeholder) {
      const s = this.speaking ? 1 + Math.sin(t * 18) * 0.06 : 1 + Math.sin(p * 2) * 0.02 * pose.sway;
      this.placeholder.scale.setScalar(s);
      this.placeholder.position.y = 1.3 + this.dockOffset;
    }

    this.renderer.render(this.scene, this.camera);
  }
}
