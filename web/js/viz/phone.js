// The phone as a calibrated camera in the scene: a frustum from the lens intrinsics and the
// board pose, the real (undistorted) video frame on its image plane, and the rectified table
// photo as an optional ground texture. From the phone's own viewpoint the frame becomes the
// backdrop and the simulation is drawn over it with the phone's exact projection.
// Approximate by construction for one camera with flagged calibration: the layer says so.
import * as THREE from 'three';
import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

const loader = new THREE.TextureLoader();

function loadTex(url) {
  return new Promise((res, rej) => loader.load(url, (t) => { t.colorSpace = THREE.SRGBColorSpace; res(t); }, undefined, rej));
}

export class PhoneLayer {
  constructor() {
    this.group = new THREE.Group(); this.group.name = 'phone';
    this.view = null; this.ground = null; this.backdrop = null;
  }

  clear() {
    for (const c of [...this.group.children]) { this.group.remove(c); c.traverse((o) => { o.geometry?.dispose?.(); if (o.isCSS2DObject) o.element.remove(); }); }
    if (this.ground) { this.ground.parent?.remove(this.ground); this.ground = null; }
    this.view = null;
  }

  // view: mirror-capture-view/1 (see docs in status/viz3d.md); base: URL prefix for its files.
  async build(view, base = '') {
    this.clear();
    if (!view?.camera) return;
    this.view = view;
    const cam = view.camera;
    const R = new THREE.Matrix3().set(...cam.R_wc.flat());   // camera (OpenCV) -> world
    const C = new THREE.Vector3(...cam.C_w);
    this.C = C; this.R = R;
    const toWorld = (u, v, d) => new THREE.Vector3((u - cam.cx) / cam.fx * d, (v - cam.cy) / cam.fy * d, d).applyMatrix3(R).add(C);
    this.toWorld = toWorld;
    const d = 0.2;
    const corners = [toWorld(0, 0, d), toWorld(cam.width, 0, d), toWorld(cam.width, cam.height, d), toWorld(0, cam.height, d)];
    const ink = new THREE.LineBasicMaterial({ color: '#5b5e66', transparent: true, opacity: 0.85 });
    const pts = [];
    for (const k of corners) pts.push(C, k);
    for (let i = 0; i < 4; i++) pts.push(corners[i], corners[(i + 1) % 4]);
    this.group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), ink));
    // Optical axis to the table plane, dashed.
    const fwd = new THREE.Vector3(0, 0, 1).applyMatrix3(R);
    if (fwd.z < -1e-3) {
      const hit = C.clone().addScaledVector(fwd, -C.z / fwd.z);
      const ax = new THREE.Line(new THREE.BufferGeometry().setFromPoints([C, hit]), new THREE.LineDashedMaterial({ color: '#5b5e66', dashSize: 0.01, gapSize: 0.008, transparent: true, opacity: 0.6 }));
      ax.computeLineDistances();
      this.group.add(ax);
    }
    // Phone body: a 7.5 x 15 x 0.8 cm slab behind the lens, aligned with the image axes.
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.075, 0.15, 0.008), new THREE.MeshStandardMaterial({ color: '#2b2d31', roughness: 0.35, metalness: 0.6 }));
    const basis = new THREE.Matrix4().setFromMatrix3(R);
    body.quaternion.setFromRotationMatrix(basis);
    body.position.copy(C).addScaledVector(fwd, -0.006);
    this.group.add(body);
    const div = document.createElement('div');
    div.className = 'label3d viz-phone';
    const span = document.createElement('span');
    span.textContent = `phone · f ${cam.fx.toFixed(0)} px · ${(C.z * 100).toFixed(0)} cm above table · approximate`;
    div.appendChild(span);
    const lab = new CSS2DObject(div); lab.position.copy(C).add(new THREE.Vector3(0, 0, 0.03)); lab.userData.isLabel = true;
    this.group.add(lab);

    const quad = (cs) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(cs.flatMap((v) => [v.x, v.y, v.z]), 3));
      g.setAttribute('uv', new THREE.Float32BufferAttribute([0, 1, 1, 1, 1, 0, 0, 0], 2));
      g.setIndex([0, 3, 1, 1, 3, 2]);
      return g;
    };
    if (view.frame?.url) {
      try {
        const tex = await loadTex(base + view.frame.url);
        this.frameTex = tex;
        const plane = new THREE.Mesh(quad(corners), new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, transparent: true, opacity: 0.92, toneMapped: false }));
        this.group.add(plane);
        // Backdrop for the phone viewpoint: same frame on a far plane, drawn first, no depth.
        const far = 6;
        const fc = [toWorld(0, 0, far), toWorld(cam.width, 0, far), toWorld(cam.width, cam.height, far), toWorld(0, cam.height, far)];
        this.backdrop = new THREE.Mesh(quad(fc), new THREE.MeshBasicMaterial({ map: tex, depthTest: false, depthWrite: false, toneMapped: false, side: THREE.DoubleSide }));
        this.backdrop.renderOrder = -10; this.backdrop.visible = false;
        this.backdrop.frustumCulled = false;
        this.group.add(this.backdrop);
      } catch (e) { console.warn('capture frame not loaded', e); }
    }
    if (view.table?.url) {
      try {
        const tex = await loadTex(base + view.table.url);
        tex.anisotropy = 8;
        const [x0, x1] = view.table.x_range, [y0, y1] = view.table.y_range;
        const z = (view.table.z || 0) + 0.0006;
        const g = new THREE.PlaneGeometry(x1 - x0, y1 - y0);
        this.ground = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.9, depthWrite: false, toneMapped: false }));
        this.ground.position.set((x0 + x1) / 2, (y0 + y1) / 2, z);
        this.ground.renderOrder = 0;
        this.ground.receiveShadow = false;
        this.ground.name = 'table-twin';
      } catch (e) { console.warn('table texture not loaded', e); }
    }
  }

  // Projection matrix equal to the phone's pinhole model (principal point honoured).
  projection(near = 0.01, far = 20) {
    const c = this.view.camera, W = c.width, H = c.height;
    const m = new THREE.Matrix4();
    // OpenCV pixel coords -> NDC with y up; camera looks down -z in three, so flip y and z axes.
    m.set(2 * c.fx / W, 0, 1 - 2 * c.cx / W, 0,
      0, 2 * c.fy / H, 2 * c.cy / H - 1, 0,
      0, 0, -(far + near) / (far - near), -2 * far * near / (far - near),
      0, 0, -1, 0);
    return m;
  }

  // Pose a three camera at the phone (three camera axes: x right, y up, looking along -z).
  poseCamera(camera) {
    const R = this.R;
    const e = R.elements;   // column-major: columns are camera axes in world
    const x = new THREE.Vector3(e[0], e[1], e[2]), y = new THREE.Vector3(e[3], e[4], e[5]), z = new THREE.Vector3(e[6], e[7], e[8]);
    const m = new THREE.Matrix4().makeBasis(x, y.clone().negate(), z.clone().negate());
    camera.position.copy(this.C);
    camera.quaternion.setFromRotationMatrix(m);
    camera.updateMatrixWorld(true);
  }

  aspect() { return this.view ? this.view.camera.width / this.view.camera.height : 1; }
}
