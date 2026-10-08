import { useEffect, useRef } from "react";
import {
  AdditiveBlending, BufferGeometry, Color, Float32BufferAttribute, Group, IcosahedronGeometry, Mesh, NormalBlending, PerspectiveCamera, Points,
  Scene, ShaderMaterial, WebGLRenderer,
} from "three";
import type { CoreTone, CoreVariant } from "./aiCoreTypes";

/**
 * The Copilot's hero "AI Core" - its own lazily loaded chunk (three.js, ~135 KB gzip; loaded when the browser is idle).
 * Plain three.js rather than react-three-fiber: r3f pulls the whole three namespace (~235 KB gzip), over the 180 KB
 * budget for this chunk. Colour follows the market regime: bullish = the "up" token, bearish = "down", neutral = the
 * AI violet. `lowPower` (phones) draws fewer particles at 30 fps. The loop stops while the canvas is off screen or the
 * tab is hidden. Callers render AICoreFallback instead when motion is reduced or WebGL is missing.
 */

const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 permute(vec4 x){return mod289(((x*34.)+1.)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1./6.,1./3.);const vec4 D=vec4(0.,.5,1.,2.);
  vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
  float n_=.142857142857;vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.*x_);
  vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.+1.;vec4 s1=floor(b1)*2.+1.;vec4 sh=-step(h,vec4(0.));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.);m=m*m;
  return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const ORB_VERTEX = /* glsl */ `
uniform float uTime; varying vec3 vNormal; varying vec3 vView; varying float vNoise;
${NOISE}
void main(){
  float n = snoise(normal * 1.6 + vec3(uTime * .25));
  vNoise = n;
  vec3 pos = position + normal * n * .12;
  vec4 mv = modelViewMatrix * vec4(pos, 1.);
  vNormal = normalize(normalMatrix * normal); vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

const ORB_FRAGMENT = /* glsl */ `
uniform vec3 uA; uniform vec3 uB; uniform float uLight; varying vec3 vNormal; varying vec3 vView; varying float vNoise;
void main(){
  float fres = pow(1. - max(dot(vNormal, vView), 0.), 2.2);
  vec3 col = mix(uA, uB, smoothstep(-.6, .8, vNoise));
  // Dark theme: additive glow. Light theme: no darkening - the colour itself, a touch lighter, more opaque at the rim.
  vec3 dark = col * (.55 + fres * 1.2);
  vec3 light = mix(col, vec3(1.), .18 + (1. - fres) * .25);
  float a = mix(.18 + fres * .9, .22 + fres * .7, uLight);
  gl_FragColor = vec4(mix(dark, light, uLight), a);
}`;

const POINTS_VERTEX = /* glsl */ `
uniform float uTime; uniform float uSize; attribute float aSeed; varying float vMix; varying float vAlpha;
${NOISE}
void main(){
  vec3 p = position;
  float n = snoise(p * 1.4 + vec3(uTime * .18 + aSeed));
  p *= 1. + n * .07;
  vec4 mv = modelViewMatrix * vec4(p, 1.);
  vMix = aSeed; vAlpha = .45 + .55 * smoothstep(-1., 1., n);
  gl_PointSize = uSize * (1. + n * .4) / -mv.z;
  gl_Position = projectionMatrix * mv;
}`;

const POINTS_FRAGMENT = /* glsl */ `
uniform vec3 uA; uniform vec3 uB; uniform float uLight; varying float vMix; varying float vAlpha;
void main(){
  float d = length(gl_PointCoord - .5);
  if (d > .5) discard;
  float soft = smoothstep(.5, 0., d);
  gl_FragColor = vec4(mix(mix(uA, uB, vMix), vec3(1.), uLight * .2), soft * vAlpha * mix(1., .75, uLight));
}`;

function cssColor(name: string, fallback: string): Color {
  const raw = typeof document === "undefined" ? "" : getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  return raw ? new Color(`rgb(${raw.split(/\s+/).join(",")})`) : new Color(fallback);
}

function toneColors(tone: CoreTone): [Color, Color] {
  const ai = cssColor("ai", "#a78bfa");
  const cyan = cssColor("ai-2", "#22d3ee");
  if (tone === "bullish") return [cssColor("up", "#22c55e"), cyan];
  if (tone === "bearish") return [cssColor("down", "#ef4444"), ai];
  return [ai, cyan];
}

/** Points spread evenly on a sphere (Fibonacci lattice), with a second, sparser outer shell. */
function spherePoints(count: number): { positions: number[]; seeds: number[] } {
  const positions: number[] = [];
  const seeds: number[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const shell = i % 7 === 0 ? 1.45 : 1.05;
    positions.push(Math.cos(golden * i) * r * shell, y * shell, Math.sin(golden * i) * r * shell);
    const h = Math.sin(i * 12.9898) * 43758.5453;
    seeds.push(h - Math.floor(h));            // a stable pseudo-random 0..1 per point
  }
  return { positions, seeds };
}

export interface AICore3DProps { tone: CoreTone; variant?: CoreVariant; lowPower?: boolean; light?: boolean; className?: string }

export default function AICore3D({ tone, variant = "orb", lowPower = false, light = false, className }: AICore3DProps) {
  const host = useRef<HTMLDivElement>(null);
  const uniforms = useRef<{ uA: { value: Color }; uB: { value: Color }; uLight: { value: number } } | null>(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ antialias: !lowPower, alpha: true, powerPreference: lowPower ? "low-power" : "default" });
    } catch {
      el.dataset.webgl = "failed";
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, lowPower ? 1.5 : 2));
    renderer.setClearColor(0x000000, 0);
    renderer.domElement.setAttribute("aria-hidden", "true");
    renderer.domElement.style.display = "block";
    el.appendChild(renderer.domElement);

    const scene = new Scene();
    const camera = new PerspectiveCamera(38, 1, 0.1, 50);
    camera.position.set(0, 0, 5.2);
    const group = new Group();
    scene.add(group);

    const [a, b] = toneColors(tone);
    const shared = { uA: { value: a }, uB: { value: b }, uLight: { value: light ? 1 : 0 } };
    uniforms.current = shared;
    const time = { value: 0 };

    const disposables: { dispose: () => void }[] = [];
    if (variant === "orb") {
      const geo = new IcosahedronGeometry(1, lowPower ? 24 : 48);
      const mat = new ShaderMaterial({ uniforms: { ...shared, uTime: time }, vertexShader: ORB_VERTEX, fragmentShader: ORB_FRAGMENT,
                                       transparent: true, depthWrite: false, blending: light ? NormalBlending : AdditiveBlending });
      group.add(new Mesh(geo, mat));
      disposables.push(geo, mat);
      const wire = new IcosahedronGeometry(1.32, 2);
      const wireMat = new ShaderMaterial({ uniforms: { ...shared, uTime: time }, vertexShader: ORB_VERTEX, fragmentShader: ORB_FRAGMENT,
                                           transparent: true, wireframe: true, depthWrite: false });
      group.add(new Mesh(wire, wireMat));
      disposables.push(wire, wireMat);
    }
    const { positions, seeds } = spherePoints(variant === "orb" ? (lowPower ? 500 : 1200) : (lowPower ? 1400 : 3600));
    const pGeo = new BufferGeometry();
    pGeo.setAttribute("position", new Float32BufferAttribute(positions, 3));
    pGeo.setAttribute("aSeed", new Float32BufferAttribute(seeds, 1));
    const pMat = new ShaderMaterial({ uniforms: { ...shared, uTime: time, uSize: { value: variant === "orb" ? 34 : 30 } },
                                      vertexShader: POINTS_VERTEX, fragmentShader: POINTS_FRAGMENT, transparent: true, depthWrite: false,
                                      blending: light ? NormalBlending : AdditiveBlending });
    group.add(new Points(pGeo, pMat));
    disposables.push(pGeo, pMat);

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = `${w}px`;
      renderer.domElement.style.height = `${h}px`;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    ro?.observe(el);

    let visible = true;
    const io = typeof IntersectionObserver !== "undefined" ? new IntersectionObserver(([e]) => { visible = e.isIntersecting; }) : null;
    io?.observe(el);

    // The loop: 30 fps on phones, 60 elsewhere; nothing drawn while off screen or in a hidden tab.
    const minFrame = 1000 / (lowPower ? 30 : 60);
    let raf = 0;
    let last = 0;
    const start = performance.now();
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (!visible || document.hidden || now - last < minFrame - 1) return;
      last = now;
      const t = (now - start) / 1000;
      time.value = t;
      group.rotation.y = t * 0.18;
      group.rotation.x = Math.sin(t * 0.3) * 0.12;
      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(loop);
    el.dataset.webgl = "on";

    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      io?.disconnect();
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
      renderer.domElement.remove();
      uniforms.current = null;
    };
  }, [variant, lowPower]); // eslint-disable-line react-hooks/exhaustive-deps

  // A regime or theme change recolours the running scene.
  useEffect(() => {
    const u = uniforms.current;
    if (!u) return;
    const [a, b] = toneColors(tone);
    u.uA.value = a;
    u.uB.value = b;
    u.uLight.value = light ? 1 : 0;
  }, [tone, light]);

  return <div ref={host} className={className} data-testid="ai-core-3d" />;
}
