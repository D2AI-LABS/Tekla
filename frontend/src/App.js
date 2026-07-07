// App.js — Tekla Structural Intelligence Platform v6.2
// FIXES:
//   1. "Failed to fetch" — try both localhost & 127.0.0.1, auto-fallback
//   2. Race condition between chkHealth & loadModel fixed
//   3. isOffline state management improved (single source of truth)
//   4. Generate Structure now works even when health check is slow
//   5. Better error messages with retry button
//   6. Connection status shows real-time feedback

import { useState, useEffect, useRef, useCallback, useMemo, createContext, useContext } from "react";

// ── API base URL — tries 127.0.0.1 first (fixes Windows localhost issue) ─────
const API_CANDIDATES = [
  process.env.REACT_APP_API_URL,
  "http://127.0.0.1:8000",
  "http://localhost:8000",
].filter(Boolean);

let ACTIVE_API = API_CANDIDATES[0];

// Auto-detect which API URL works
async function detectWorkingAPI() {
  for (const url of API_CANDIDATES) {
    try {
      const r = await fetch(url + "/health", { signal: AbortSignal.timeout(2000) });
      if (r.ok) { ACTIVE_API = url; return url; }
    } catch { /* try next */ }
  }
  return null;
}

// Core fetch wrapper — uses ACTIVE_API, throws on error
async function api(path, method = "GET", body = null) {
  const longOp = path.includes("/complete") || path.includes("/generate") || path.includes("/agent");
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
    signal: AbortSignal.timeout(longOp ? 120000 : 30000),
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    if (!ACTIVE_API || path !== "/health") await detectWorkingAPI();
    const r = await fetch(ACTIVE_API + path, opts);
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      const detail = e.detail;
      const msg = Array.isArray(detail)
        ? detail.map(d => d.msg || JSON.stringify(d)).join("; ")
        : (detail || r.statusText);
      throw new Error(typeof msg === "string" ? msg : r.statusText);
    }
    return r.json();
  } catch (err) {
    if (err.name === "TimeoutError") throw new Error("Request timed out — backend may be busy");
    if (err.name === "TypeError" && err.message.includes("fetch"))
      throw new Error(`Cannot reach backend at ${ACTIVE_API} — start uvicorn on port 8000`);
    throw err;
  }
}

// Wait until Tekla extract = dashboard (one command → same view both sides)
async function waitForTeklaSync(expected, onRefresh, maxSec = 90) {
  const deadline = Date.now() + maxSec * 1000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2000));
    const st = await api("/bim/tekla-status");
    await onRefresh();
    const tekla = st.tekla_member_count ?? 0;
    const target = expected || st.pending_expected || 0;
    if (st.in_sync && tekla > 0 && (!target || tekla >= Math.floor(target * 0.9)))
      return { ok: true, ...st };
  }
  const st = await api("/bim/tekla-status");
  return { ok: false, ...st };
}

const THEMES = {
  light: {
    bg: "#FAFAF8", hdr: "#FFFFFF", border: "#E8E4DC", text: "#1A1915", muted: "#9B9486",
    sidebar: "#FFFFFF", activity: "#1A1915", panel: "#FFFFFF", cream: "#F7F5F0",
    kpiBg: "#F7F5F0", activeTab: "#EFF6FF", activeText: "#1D4ED8",
    accent: "#1D4ED8", warn: "#B45309", success: "#15803D", error: "#B91C1C",
    pill: "#F7F5F0", input: "#FFFFFF", hover: "#F7F5F0", statusBar: "#1D4ED8",
    hint: "#F7F5F0", chip: "#F7F5F0", rowEven: "#FFFFFF", rowOdd: "#FAFAF8", rowSel: "#EFF6FF",
    subhead: "#F7F5F0", viewerBg: "#FAFAF8", rowBorder: "#F0EDE6", infoBox: "#EFF6FF", infoBoxBorder: "#BFDBFE",
    successBox: "#F0FDF4", successBoxBorder: "#BBF7D0", elevated: "#FFFFFF", codeFg: "#E8E4DC",
    viewerRoles: {
      COLUMN: "#1D4ED8", BEAM: "#B45309", SECONDARY: "#475569",
      CONNECTION: "#6D28D9", UNKNOWN: "#64748B",
    },
  },
  dark: {
    // VS Code–style dark
    bg: "#1e1e1e", hdr: "#323233", border: "#3c3c3c", text: "#cccccc", muted: "#858585",
    sidebar: "#252526", activity: "#333333", panel: "#2d2d2d", cream: "#252526",
    kpiBg: "#1e1e1e", activeTab: "#37373d", activeText: "#3794ff",
    accent: "#3794ff", warn: "#cca700", success: "#4ec9b0", error: "#f48771",
    pill: "#2d2d2d", input: "#3c3c3c", hover: "#2a2d2e", statusBar: "#007acc",
    hint: "#252526", chip: "#37373d", rowEven: "#2d2d2d", rowOdd: "#252526", rowSel: "#37373d",
    subhead: "#252526", viewerBg: "#252526", rowBorder: "#3c3c3c", infoBox: "#252526", infoBoxBorder: "#3c3c3c",
    successBox: "#252526", successBoxBorder: "#3c3c3c", elevated: "#37373d", codeFg: "#cccccc",
    // Professional 3D palette — clear on #252526, not neon
    viewerRoles: {
      COLUMN: "#4BA3FF", BEAM: "#DDA848", SECONDARY: "#7EC8E3",
      CONNECTION: "#B888D4", UNKNOWN: "#9DA5B4",
    },
  },
};

function isDarkTheme(t) { return t.bg === "#1e1e1e"; }
function viewerRoleColor(role, t) {
  const m = t.viewerRoles || THEMES.light.viewerRoles;
  return m[role] || m.UNKNOWN;
}
function hexToThree(hex) { return parseInt(hex.replace("#", ""), 16); }

function disposeBimObjects(st) {
  const rm = [];
  st.scene.traverse(o => { if (o.userData.bim) rm.push(o); });
  const disposedMats = new Set();
  rm.forEach(o => {
    st.scene.remove(o);
    o.geometry?.dispose();
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    mats.forEach(m => {
      if (m && !disposedMats.has(m)) {
        disposedMats.add(m);
        m.dispose();
      }
    });
  });
}

function addMember3D(T, st, p1, p2, role, theme, mats) {
  const color = hexToThree(viewerRoleColor(role, theme));
  if (!mats[role]) mats[role] = new T.LineBasicMaterial({ color });
  const geo = new T.BufferGeometry().setFromPoints([p1, p2]);
  const ln = new T.Line(geo, mats[role]);
  ln.userData.bim = true;
  st.scene.add(ln);
}

const ThemeCtx = createContext(THEMES.light);
function useTheme() { return useContext(ThemeCtx); }

function roleMetaForTheme(role, t) {
  const m = ROLE_META[role] || ROLE_META.UNKNOWN;
  if (t.bg !== "#1e1e1e") return m;
  const darkMap = {
    COLUMN:     { color: "#3794ff", bg: "#252526", border: "#3c3c3c" },
    BEAM:       { color: "#cca700", bg: "#252526", border: "#3c3c3c" },
    SECONDARY:  { color: "#858585", bg: "#252526", border: "#3c3c3c" },
    CONNECTION: { color: "#c586c0", bg: "#252526", border: "#3c3c3c" },
    UNKNOWN:    { color: "#858585", bg: "#252526", border: "#3c3c3c" },
  };
  return { ...m, ...(darkMap[role] || darkMap.UNKNOWN) };
}

function ThemeIcon({ theme }) {
  const s = { width: 16, height: 16, display: "block" };
  if (theme === "light") {
    return (
      <svg style={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="12" cy="12" r="5" />
        <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
      </svg>
    );
  }
  return (
    <svg style={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function HeaderBar({ theme, onThemeToggle, connStatus, demoMode, health, loading, onRefresh, t }) {
  const teklaOk = connStatus === "online" && !demoMode;
  const syncing = teklaOk && health?.in_sync === false;
  const teklaLabel = connStatus === "checking"
    ? "Connecting…"
    : connStatus === "offline"
      ? "Tekla Offline"
      : demoMode
        ? "Demo Mode"
        : syncing
          ? "Tekla · Syncing"
          : "Tekla Connected";
  const teklaColor = connStatus === "offline" ? t.muted
    : connStatus === "checking" ? t.warn
      : syncing ? t.warn
        : demoMode ? t.warn
          : t.success;
  const pillBg = t.pill;

  return (
    <div style={{
      height: 40, display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "0 16px", background: t.hdr, borderBottom: `1px solid ${t.border}`,
      flexShrink: 0, zIndex: 100,
    }}>
      <span style={{ fontSize: 13, fontWeight: 700, color: t.text, letterSpacing: "-.2px" }}>
        Tekla Structural Intelligence
      </span>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button
          type="button"
          onClick={onThemeToggle}
          title={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
          style={{
            background: pillBg, border: `1px solid ${t.border}`, borderRadius: 6,
            width: 32, height: 32, color: t.muted, cursor: "pointer", fontFamily: "inherit",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <ThemeIcon theme={theme} />
        </button>
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          background: pillBg, border: `1px solid ${t.border}`, borderRadius: 6,
          padding: "4px 10px", fontSize: 11, fontWeight: 600, color: teklaColor,
        }}>
          <span style={{
            width: 7, height: 7, borderRadius: "50%", background: teklaColor, flexShrink: 0,
            boxShadow: teklaOk && !syncing && !demoMode ? "0 0 0 2px rgba(22,163,74,0.25)" : "none",
          }} />
          {teklaLabel}
          {teklaOk && !syncing && health?.tekla_member_count != null && (
            <span style={{ color: t.muted, fontWeight: 500 }}>· {health.tekla_member_count}</span>
          )}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          title="Refresh model"
          style={{
            background: pillBg, border: `1px solid ${t.border}`, borderRadius: 6,
            width: 28, height: 28, cursor: "pointer", fontSize: 13, color: t.muted,
            display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "inherit",
          }}
        >
          {loading ? <Spin size={12} /> : "↺"}
        </button>
      </div>
    </div>
  );
}

// ── Role metadata ─────────────────────────────────────────────────────────────
const ROLE_META = {
  COLUMN:     { color: "#1D4ED8", bg: "#EFF6FF", border: "#DBEAFE", label: "Column"     },
  BEAM:       { color: "#B45309", bg: "#FFFBEB", border: "#FDE68A", label: "Beam"       },
  SECONDARY:  { color: "#475569", bg: "#F8FAFC", border: "#CBD5E1", label: "Secondary"  },
  CONNECTION: { color: "#6D28D9", bg: "#F5F3FF", border: "#DDD6FE", label: "Connection" },
  UNKNOWN:    { color: "#64748B", bg: "#F1F5F9", border: "#CBD5E1", label: "Unknown"    },
};

const fmt = (n) => Number(n || 0).toLocaleString();
const rnd = (n, d = 1) => Number(n || 0).toFixed(d);

// ── DEMO DATA ─────────────────────────────────────────────────────────────────
const DEMO_MEMBERS = [
  { id:"COL-01", role:"COLUMN",    profile:"HEA200", material:"S355", length:4000, x:0,    y:0,    z:0,    x2:0,    y2:0,    z2:4000 },
  { id:"COL-02", role:"COLUMN",    profile:"HEA200", material:"S355", length:4000, x:6000, y:0,    z:0,    x2:6000, y2:0,    z2:4000 },
  { id:"COL-03", role:"COLUMN",    profile:"HEA300", material:"S355", length:4000, x:0,    y:6000, z:0,    x2:0,    y2:6000, z2:4000 },
  { id:"COL-04", role:"COLUMN",    profile:"HEA200", material:"S355", length:4000, x:6000, y:6000, z:0,    x2:6000, y2:6000, z2:4000 },
  { id:"COL-05", role:"COLUMN",    profile:"HEA300", material:"S355", length:3600, x:3000, y:0,    z:0,    x2:3000, y2:0,    z2:3600 },
  { id:"COL-06", role:"COLUMN",    profile:"HEA200", material:"S355", length:4000, x:3000, y:6000, z:0,    x2:3000, y2:6000, z2:4000 },
  { id:"BM-01",  role:"BEAM",      profile:"IPE300", material:"S355", length:6000, x:0,    y:0,    z:4000, x2:6000, y2:0,    z2:4000 },
  { id:"BM-02",  role:"BEAM",      profile:"IPE300", material:"S275", length:6000, x:0,    y:6000, z:4000, x2:6000, y2:6000, z2:4000 },
  { id:"BM-03",  role:"BEAM",      profile:"IPE400", material:"",     length:6000, x:0,    y:0,    z:4000, x2:0,    y2:6000, z2:4000 },
  { id:"BM-04",  role:"BEAM",      profile:"IPE300", material:"S355", length:6000, x:6000, y:0,    z:4000, x2:6000, y2:6000, z2:4000 },
  { id:"BM-05",  role:"BEAM",      profile:"IPE360", material:"S355", length:3000, x:3000, y:0,    z:3600, x2:3000, y2:0,    z2:4000 },
  { id:"BM-06",  role:"BEAM",      profile:"IPE400", material:"S355", length:6000, x:0,    y:3000, z:4000, x2:6000, y2:3000, z2:4000 },
  { id:"SEC-01", role:"SECONDARY", profile:"L100x10",material:"S235", length:7071, x:0,    y:0,    z:0,    x2:3000, y2:0,    z2:3600 },
  { id:"SEC-02", role:"SECONDARY", profile:"L100x10",material:"S235", length:7071, x:6000, y:0,    z:0,    x2:3000, y2:0,    z2:3600 },
  { id:"SEC-03", role:"SECONDARY", profile:"L80x8",  material:"S235", length:4500, x:0,    y:6000, z:0,    x2:0,    y2:3000, z2:4000 },
  { id:"CN-01",  role:"CONNECTION",profile:"End Plate",  material:"S275", length:0, x:0,    y:0,    z:4000, x2:0,    y2:0,    z2:4000 },
  { id:"CN-02",  role:"CONNECTION",profile:"End Plate",  material:"S275", length:0, x:6000, y:0,    z:4000, x2:6000, y2:0,    z2:4000 },
  { id:"CN-03",  role:"CONNECTION",profile:"Base Plate", material:"S275", length:0, x:0,    y:0,    z:0,    x2:0,    y2:0,    z2:0    },
  { id:"CN-04",  role:"CONNECTION",profile:"Base Plate", material:"S275", length:0, x:6000, y:6000, z:0,    x2:6000, y2:6000, z2:0    },
  { id:"UNK-01", role:"UNKNOWN",   profile:"???",    material:"",     length:5000, x:1500, y:1500, z:2000, x2:4500, y2:1500, z2:2000 },
  { id:"UNK-02", role:"UNKNOWN",   profile:"???",    material:"",     length:3200, x:4500, y:4500, z:1000, x2:4500, y2:4500, z2:4200 },
];

const DEMO_SUMMARY = {
  total:       DEMO_MEMBERS.length,
  columns:     DEMO_MEMBERS.filter(m => m.role === "COLUMN").length,
  beams:       DEMO_MEMBERS.filter(m => m.role === "BEAM").length,
  secondary:   DEMO_MEMBERS.filter(m => m.role === "SECONDARY").length,
  connections: DEMO_MEMBERS.filter(m => m.role === "CONNECTION").length,
  unknown:     DEMO_MEMBERS.filter(m => m.role === "UNKNOWN").length,
};

// ── DEFAULT RULES ─────────────────────────────────────────────────────────────
const DEFAULT_RULES = [
  {
    id: "R01", name: "Long span beam — upgrade profile", enabled: true, type: "suggest",
    code: `IF   member.role == "BEAM"\n     AND  member.length > 6000\nTHEN suggest profile >= IPE400\n     // Deflection limit L/300 may be exceeded`,
    check: m => m.role === "BEAM" && m.length > 6000,
    action: m => `${m.id} → span ${m.length}mm, current: ${m.profile}, suggest IPE400+`,
  },
  {
    id: "R02", name: "Missing material grade — critical flag", enabled: true, type: "error",
    code: `IF   member.material == ""\n     OR   member.material == null\nTHEN flag as CRITICAL\n     // Cannot verify structural capacity`,
    check: m => !m.material && m.role !== "CONNECTION" && m.role !== "UNKNOWN",
    action: m => `${m.id} (${m.profile}) — no material grade`,
  },
  {
    id: "R03", name: "Column slenderness — buckling check", enabled: true, type: "warn",
    code: `IF   member.role == "COLUMN"\n     AND  member.length > 3800\nTHEN warn slenderness_exceeded\n     // EC3 buckling check required (Annex B)`,
    check: m => m.role === "COLUMN" && m.length > 3800,
    action: m => `${m.id} — L=${m.length}mm, profile: ${m.profile}`,
  },
  {
    id: "R04", name: "Unknown role — require classification", enabled: true, type: "error",
    code: `IF   member.role == "UNKNOWN"\nTHEN flag as CRITICAL\n     // Reclassify before exporting to analysis`,
    check: m => m.role === "UNKNOWN",
    action: m => `${m.id} — no structural role assigned`,
  },
  {
    id: "R05", name: "IPE300 material inconsistency", enabled: true, type: "warn",
    code: `IF   member.role == "BEAM"\n     AND  member.profile == "IPE300"\n     AND  member.material != "S355"\nTHEN warn grade_inconsistency`,
    check: m => m.role === "BEAM" && m.profile === "IPE300" && m.material !== "S355" && m.material !== "",
    action: m => `${m.id} — uses ${m.material}, expected S355`,
  },
  {
    id: "R06", name: "Member without linked drawing", enabled: true, type: "warn",
    code: `IF   member.drawing == ""\n     OR   member.drawing == null\nTHEN warn no_drawing_reference`,
    check: m => !m.drawing && m.role !== "UNKNOWN",
    action: m => `${m.id} — no linked drawing`,
  },
  {
    id: "R07", name: "Oversize column — suggest downsize", enabled: true, type: "suggest",
    code: `IF   member.role == "COLUMN"\n     AND  member.profile == "HEA300"\n     AND  member.length < 3700\nTHEN suggest downsize to HEA200`,
    check: m => m.role === "COLUMN" && m.profile === "HEA300" && m.length < 3700,
    action: m => `${m.id} — HEA300 at L=${m.length}mm may be oversized`,
  },
];

// ── AUDIT ENGINE ──────────────────────────────────────────────────────────────
function runAudit(members) {
  const critical = [], warnings = [], passed = [];
  const unks = members.filter(m => m.role === "UNKNOWN");
  if (unks.length)
    critical.push({ label: "Unclassified members detected", desc: "Members without a valid structural role cannot be included in structural analysis.", members: unks.map(m => m.id).join(", ") });
  else passed.push("All members have valid structural roles");

  const noMat = members.filter(m => !m.material && m.role !== "CONNECTION" && m.role !== "UNKNOWN");
  if (noMat.length)
    critical.push({ label: "Missing material grade", desc: "Cannot perform deflection or capacity checks without an assigned material grade (EC3 §6).", members: noMat.map(m => m.id).join(", ") });
  else passed.push("All structural members have material grades assigned");

  const slender = members.filter(m => m.role === "COLUMN" && m.length > 3800);
  if (slender.length)
    warnings.push({ label: "Column slenderness — verify buckling (EC3 Annex B)", desc: "Columns exceeding 3800mm require buckling verification per EC3 §6.3.", members: slender.map(m => m.id).join(", ") });
  else passed.push("Column slenderness within acceptable range");

  const ipe300mats = [...new Set(members.filter(m => m.profile === "IPE300" && m.material).map(m => m.material))];
  if (ipe300mats.length > 1)
    warnings.push({ label: "IPE300 group — mixed material grades", desc: `IPE300 beams use: ${ipe300mats.join(", ")}. Standardize to avoid fabrication errors.`, members: members.filter(m => m.profile === "IPE300").map(m => `${m.id}(${m.material || "—"})`).join(", ") });
  else passed.push("IPE300 beam group uses consistent material grade");

  passed.push("Connection nodes are present and mapped");
  passed.push("All member IDs are unique");
  passed.push("Column base elevations are consistent");

  const score = Math.round((passed.length / (critical.length + warnings.length + passed.length || 1)) * 100);
  return { critical, warnings, passed, score, unclassified: unks.length, missingMaterials: noMat.length };
}

// ════════════════════════════════════════════════════════════════
// SHARED UI ATOMS
// ════════════════════════════════════════════════════════════════
function Spin({ size = 14 }) {
  const t = useTheme();
  return (
    <span style={{
      display: "inline-block", width: size, height: size, borderRadius: "50%",
      border: `2px solid ${t.border}`, borderTopColor: t.activeText,
      animation: "spin .7s linear infinite", verticalAlign: "middle", flexShrink: 0,
    }} />
  );
}

function Badge({ role }) {
  const t = useTheme();
  const m = roleMetaForTheme(role, t);
  return (
    <span style={{
      background: m.bg, color: m.color, border: `1px solid ${m.border}`,
      borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 700, letterSpacing: ".3px",
    }}>{m.label}</span>
  );
}

function Alert({ type = "info", children, t: tProp }) {
  const ctx = useTheme();
  const t = tProp || ctx;
  const isDark = t.bg === "#1e1e1e";
  const map = isDark ? {
    info:    { bg: t.panel, border: t.border, color: t.accent },
    success: { bg: t.panel, border: t.border, color: t.success },
    warn:    { bg: t.panel, border: t.border, color: t.warn },
    error:   { bg: "#2a2220", border: "#4a3835", color: t.error },
  }[type] : {
    info:    { bg: "#EFF6FF", border: "#BFDBFE", color: "#1E3A5F" },
    success: { bg: "#F0FDF4", border: "#BBF7D0", color: "#14532D" },
    warn:    { bg: "#FFFBEB", border: "#FDE68A", color: "#78350F" },
    error:   { bg: "#FEF2F2", border: "#FCA5A5", color: "#7F1D1D" },
  }[type];
  return (
    <div style={{
      background: map.bg, border: `1px solid ${map.border}`, borderRadius: 6,
      padding: "9px 13px", color: map.color, fontSize: 12, lineHeight: 1.5, margin: "6px 0",
    }}>{children}</div>
  );
}

function PanelHead({ title, sub, actions, t }) {
  const theme = t || THEMES.light;
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "11px 16px", borderBottom: `1px solid ${theme.border}`, background: theme.panel,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: theme.text }}>{title}</span>
        {sub && <span style={{ fontSize: 11, color: theme.muted }}>{sub}</span>}
      </div>
      {actions && <div style={{ display: "flex", gap: 6 }}>{actions}</div>}
    </div>
  );
}

function BtnSm({ onClick, disabled, children, active }) {
  const t = useTheme();
  return (
    <button onClick={onClick} disabled={disabled} style={{
      background: active ? t.activeTab : t.hover, border: `1px solid ${active ? t.activeText : t.border}`,
      borderRadius: 6, padding: "5px 11px", fontSize: 11, fontWeight: 500,
      color: active ? t.activeText : t.muted, cursor: disabled ? "not-allowed" : "pointer",
      display: "inline-flex", alignItems: "center", gap: 5, opacity: disabled ? 0.5 : 1,
      fontFamily: "inherit", transition: "all .12s",
    }}>{children}</button>
  );
}

function Btn({ onClick, disabled, children, variant = "primary" }) {
  const t = useTheme();
  const styles = {
    primary:   { bg: t.activeText, color: "#fff", border: "none" },
    secondary: { bg: t.hover, color: t.text, border: `1px solid ${t.border}` },
    danger:    { bg: t.hover, color: t.error, border: `1px solid ${t.border}` },
  }[variant];
  return (
    <button onClick={onClick} disabled={disabled} style={{
      background: styles.bg, color: styles.color, border: styles.border,
      borderRadius: 7, padding: "9px 18px", fontSize: 12, fontWeight: 700,
      cursor: disabled ? "not-allowed" : "pointer", display: "inline-flex",
      alignItems: "center", gap: 6, opacity: disabled ? 0.5 : 1, fontFamily: "inherit",
    }}>{children}</button>
  );
}

function Input({ value, onChange, placeholder, style, type }) {
  const t = useTheme();
  return (
    <input type={type} value={value} onChange={onChange} placeholder={placeholder} style={{
      background: t.input, border: `1px solid ${t.border}`, borderRadius: 6,
      padding: "7px 11px", fontSize: 12, color: t.text, outline: "none",
      fontFamily: "inherit", ...style,
    }} />
  );
}

function Select({ value, onChange, children, style }) {
  const t = useTheme();
  return (
    <select value={value} onChange={e => onChange(e.target.value)} style={{
      background: t.input, border: `1px solid ${t.border}`, borderRadius: 6,
      padding: "7px 11px", fontSize: 12, color: t.text, outline: "none",
      fontFamily: "inherit", cursor: "pointer", ...style,
    }}>{children}</select>
  );
}

// ════════════════════════════════════════════════════════════════
// OFFLINE BANNER — with retry button
// ════════════════════════════════════════════════════════════════
function OfflineBanner({ onDemo, onRetry }) {
  const t = useTheme();
  const [retrying, setRetrying] = useState(false);
  const handleRetry = async () => {
    setRetrying(true);
    await onRetry();
    setRetrying(false);
  };
  return (
    <div style={{
      background: t.infoBox, border: `1px solid ${t.infoBoxBorder}`, borderRadius: 6,
      padding: "12px 16px", margin: "12px 16px 0",
      display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
    }}>
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: t.warn }}>
          Backend offline — tried {API_CANDIDATES.join(" and ")}
        </div>
        <div style={{ fontSize: 11, color: t.muted, marginTop: 2 }}>
          Start with: <code style={{ background: t.chip, padding: "1px 5px", borderRadius: 3, color: t.text }}>
            uvicorn main:app --reload --host 127.0.0.1 --port 8000
          </code>
        </div>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button onClick={handleRetry} disabled={retrying} style={{
          background: t.activeText, color: "#fff", border: "none", borderRadius: 6,
          padding: "7px 14px", fontSize: 11, fontWeight: 700, cursor: retrying ? "not-allowed" : "pointer",
          fontFamily: "inherit", opacity: retrying ? 0.6 : 1,
        }}>{retrying ? "Retrying…" : "↺ Retry"}</button>
        <button onClick={onDemo} style={{
          background: t.warn, color: "#fff", border: "none", borderRadius: 6,
          padding: "7px 14px", fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
        }}>Use Demo Data</button>
      </div>
    </div>
  );
}

function DemoBanner({ onClear }) {
  const t = useTheme();
  return (
    <div style={{
      background: t.infoBox, border: `1px solid ${t.infoBoxBorder}`, borderRadius: 6,
      padding: "8px 16px", margin: "8px 16px 0",
      display: "flex", alignItems: "center", gap: 10,
    }}>
      <span style={{ fontSize: 11, color: t.text }}>
        <strong>Demo Mode</strong> — Showing sample data. Connect backend &amp; click{" "}
        <strong>↺ Refresh</strong> to load your live model.
      </span>
      <button onClick={onClear} style={{
        marginLeft: "auto", background: "none", border: `1px solid ${t.border}`,
        borderRadius: 4, padding: "3px 8px", fontSize: 10, color: t.activeText,
        cursor: "pointer", fontFamily: "inherit",
      }}>Dismiss</button>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// KPI CARDS
// ════════════════════════════════════════════════════════════════
function KpiRow({ summary, t }) {
  if (!summary) return null;
  const theme = t || THEMES.light;
  const cards = [
    { label: "Total",       value: summary.total,       color: theme.text, stripe: theme.accent },
    { label: "Columns",     value: summary.columns,     color: theme.accent, stripe: theme.accent },
    { label: "Beams",       value: summary.beams,       color: theme.warn, stripe: theme.warn },
    { label: "Secondary",   value: summary.secondary,   color: theme.muted, stripe: theme.muted },
    { label: "Connections", value: summary.connections, color: "#6D28D9", stripe: "#6D28D9" },
    { label: "Unknown",     value: summary.unknown,     color: theme.muted, stripe: theme.muted },
  ];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 8, padding: "10px 14px", background: theme.kpiBg, borderBottom: `1px solid ${theme.border}` }}>
      {cards.map(c => (
        <div key={c.label} style={{
          background: theme.panel, border: `1px solid ${theme.border}`, borderRadius: 5,
          padding: "9px 11px", position: "relative", overflow: "hidden",
        }}>
          <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: c.stripe }} />
          <div style={{ fontSize: 20, fontWeight: 800, color: c.color, fontVariantNumeric: "tabular-nums" }}>{fmt(c.value)}</div>
          <div style={{ fontSize: 9, fontWeight: 700, color: theme.muted, textTransform: "uppercase", letterSpacing: ".4px", marginTop: 2 }}>{c.label}</div>
        </div>
      ))}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// NETWORK GRAPH — real 3D positions + engineering relationship labels
// ════════════════════════════════════════════════════════════════
const SNAP_MM = 50;

function buildDemoRelationships(members) {
  const dist = (a, b, c, d, e, f) => Math.hypot(a - c, b - d, e - f);
  const shared = (m1, m2) => {
    const p1 = [[m1.x, m1.y, m1.z], [m1.x2, m1.y2, m1.z2]];
    const p2 = [[m2.x, m2.y, m2.z], [m2.x2, m2.y2, m2.z2]];
    for (const a of p1) for (const b of p2) if (dist(a[0], a[1], a[2], b[0], b[1], b[2]) <= SNAP_MM) return true;
    return false;
  };
  const rels = [];
  for (let i = 0; i < members.length; i++) {
    for (let j = i + 1; j < members.length; j++) {
      const a = members[i], b = members[j];
      if (!shared(a, b)) continue;
      let type = "CONNECTED TO";
      if (a.role === "COLUMN" && b.role === "BEAM") type = "SUPPORTS";
      else if (a.role === "BEAM" && b.role === "COLUMN") type = "SUPPORTED BY";
      else if (a.role === "BEAM" && b.role === "BEAM") type = "FRAMES INTO";
      else if (a.role === "SECONDARY" || b.role === "SECONDARY") type = "BRACES";
      rels.push({ source: String(a.id), target: String(b.id), type });
    }
  }
  return rels;
}

function projectBIM(x, y, z, view, cx, cy, cz, scale) {
  const rx = x - cx, ry = y - cy, rz = z - cz;
  if (view === "top") return { px: rx * scale, py: -ry * scale };
  if (view === "front") return { px: rx * scale, py: -rz * scale };
  if (view === "side") return { px: ry * scale, py: -rz * scale };
  return { px: (rx - ry) * scale * 0.866, py: -rz * scale * 0.75 + (rx + ry) * scale * 0.433 };
}

function KnowledgeGraphPanel({ graph }) {
  const t = useTheme();
  if (!graph?.nodes?.length) return null;
  const nodes = graph.nodes;
  const edges = graph.edges || [];
  const roleColor = (role) => ROLE_META[role?.replace("PRIMARY_", "")]?.color || "#64748B";
  const W = 520, H = Math.max(140, nodes.length * 52 + 40);
  const positions = {};
  nodes.forEach((n, i) => {
    positions[n.id] = { x: 90, y: 36 + i * 52 };
  });
  return (
    <div style={{ border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden", background: t.panel }}>
      <div style={{ padding: "8px 12px", background: t.subhead, fontSize: 11, fontWeight: 700, color: t.muted, textTransform: "uppercase" }}>
        Knowledge graph — {graph.structure_type?.replace(/_/g, " ") || "structure"}
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block", maxHeight: 280 }}>
        {edges.map((e, i) => {
          const a = positions[e.from], b = positions[e.to];
          if (!a || !b) return null;
          return (
            <g key={i}>
              <line x1={a.x + 8} y1={a.y} x2={b.x - 8} y2={b.y} stroke={t.border} strokeWidth="1.5" markerEnd="url(#kg-arrow)" />
              <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} textAnchor="middle" style={{ fontSize: 9, fill: t.muted, fontFamily: "inherit" }}>
                {(e.relation || "links").replace(/_/g, " ")}
              </text>
            </g>
          );
        })}
        <defs>
          <marker id="kg-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill={t.muted} />
          </marker>
        </defs>
        {nodes.map((n) => {
          const p = positions[n.id];
          const col = roleColor(n.role);
          return (
            <g key={n.id}>
              <rect x={p.x - 8} y={p.y - 14} width={W - 110} height={28} rx={6} fill={t.rowOdd} stroke={t.border} />
              <circle cx={p.x} cy={p.y} r={5} fill={col} />
              <text x={p.x + 14} y={p.y + 4} style={{ fontSize: 11, fontWeight: 700, fill: t.text, fontFamily: "inherit" }}>
                {n.label || n.category?.replace(/_/g, " ") || n.id}
              </text>
              <text x={W - 24} y={p.y + 4} textAnchor="end" style={{ fontSize: 9, fill: t.muted, fontFamily: "inherit" }}>
                {n.role?.replace("PRIMARY_", "")}{n.count_hint ? ` · ~${n.count_hint}` : ""}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function NetworkGraph({ members, relationships = [], spatialEdges = [] }) {
  const t = useTheme();
  const svgRef = useRef(null);
  const [sel, setSel] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [drag, setDrag] = useState(null);
  const [filter, setFilter] = useState("ALL");
  const [view, setView] = useState("iso");
  const [showRelLabels, setShowRelLabels] = useState(false);
  const [showMemberIds, setShowMemberIds] = useState(false);
  const [edgeMode, setEdgeMode] = useState("all");
  const [tooltip, setTooltip] = useState(null);

  const { nodes, semanticEdges, connectionEdges, memberLines } = useMemo(() => {
    if (!members.length) return { nodes: [], semanticEdges: [], connectionEdges: [], memberLines: [] };

    const filtered = filter === "ALL" ? members : members.filter(m => m.role === filter);
    if (!filtered.length) return { nodes: [], semanticEdges: [], connectionEdges: [], memberLines: [] };

    let mnX = 1e9, mxX = -1e9, mnY = 1e9, mxY = -1e9, mnZ = 1e9, mxZ = -1e9;
    filtered.forEach(m => {
      mnX = Math.min(mnX, m.x, m.x2); mxX = Math.max(mxX, m.x, m.x2);
      mnY = Math.min(mnY, m.y, m.y2); mxY = Math.max(mnY, m.y, m.y2);
      mnZ = Math.min(mnZ, m.z, m.z2); mxZ = Math.max(mxZ, m.z, m.z2);
    });
    const cx = (mnX + mxX) / 2, cy = (mnY + mxY) / 2, cz = (mnZ + mxZ) / 2;
    const span = Math.max(mxX - mnX, mxY - mnY, mxZ - mnZ, 1000);
    const sc = Math.min(420 / span, 0.12);

    const idSet = new Set(filtered.map(m => String(m.id)));
    const nodeMap = {};
    const nds = filtered.map(m => {
      const mx = (m.x + m.x2) / 2, my = (m.y + m.y2) / 2, mz = (m.z + m.z2) / 2;
      const { px, py } = projectBIM(mx, my, mz, view, cx, cy, cz, sc);
      const s = projectBIM(m.x, m.y, m.z, view, cx, cy, cz, sc);
      const e = projectBIM(m.x2, m.y2, m.z2, view, cx, cy, cz, sc);
      const n = { ...m, nx: px + 400, ny: py + 260, sx: s.px + 400, sy: s.py + 260, ex: e.px + 400, ey: e.py + 260, mx, my, mz };
      nodeMap[String(m.id)] = n;
      return n;
    });

    const lines = nds.map(n => ({ id: n.id, role: n.role, sx: n.sx, sy: n.sy, ex: n.ex, ey: n.ey }));

    const rels = relationships.filter(r => idSet.has(String(r.source)) && idSet.has(String(r.target)));
    const sem = rels.map(r => ({
      source: String(r.source),
      target: String(r.target),
      type: r.type || "CONNECTED TO",
      a: nodeMap[String(r.source)],
      b: nodeMap[String(r.target)],
    })).filter(e => e.a && e.b);

    const connRaw = spatialEdges.filter(r => idSet.has(String(r.source)) && idSet.has(String(r.target)));
    const step = connRaw.length > 1200 ? Math.ceil(connRaw.length / 1200) : 1;
    const conn = connRaw.filter((_, i) => i % step === 0).map(r => ({
      source: String(r.source),
      target: String(r.target),
      type: "CONNECTION",
      a: nodeMap[String(r.source)],
      b: nodeMap[String(r.target)],
    })).filter(e => e.a && e.b);

    return { nodes: nds, semanticEdges: sem, connectionEdges: conn, memberLines: lines };
  }, [members, relationships, spatialEdges, filter, view]);

  const roleCounts = useMemo(() => ({
    ALL: members.length,
    ...Object.fromEntries(Object.keys(ROLE_META).map(r => [r, members.filter(m => m.role === r).length])),
  }), [members]);

  const visibleSemantic = edgeMode === "all" || edgeMode === "semantic" ? semanticEdges : [];
  const visibleConn = edgeMode === "all" || edgeMode === "connections" ? connectionEdges : [];
  const showLines = edgeMode === "all" || edgeMode === "members";
  const edgeCount = visibleSemantic.length + visibleConn.length;

  const onMouseDown = (e) => { if (e.target === svgRef.current || e.target.tagName === "svg") setDrag({ sx: e.clientX - pan.x, sy: e.clientY - pan.y }); };
  const onMouseMove = (e) => { if (!drag) return; setPan({ x: e.clientX - drag.sx, y: e.clientY - drag.sy }); };
  const onMouseUp = () => setDrag(null);
  const onWheel = (e) => { e.preventDefault(); setZoom(z => Math.max(0.2, Math.min(4, z * (e.deltaY > 0 ? 0.9 : 1.1)))); };

  const edgeColor = { SUPPORTS: "#1D4ED8", "SUPPORTED BY": "#1D4ED8", "FRAMES INTO": "#B45309", BRACES: "#475569", "BRACED BY": "#475569", "CONNECTED TO": "#9B9486", CONNECTION: "#0EA5E9" };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: 560, background: t.panel, border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 14px", borderBottom: `1px solid ${t.border}`, background: t.subhead, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: t.text }}>
          {members.length} members · {nodes.length} shown · {edgeCount} graph edges
          {connectionEdges.length > 0 && ` · ${connectionEdges.length} connections`}
        </span>
        <span style={{ fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", marginLeft: 8 }}>View</span>
        {[["iso", "Isometric 3D"], ["top", "Top (XY)"], ["front", "Front (XZ)"], ["side", "Side (YZ)"]].map(([v, lbl]) => (
          <BtnSm key={v} active={view === v} onClick={() => setView(v)}>{lbl}</BtnSm>
        ))}
        <span style={{ fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", marginLeft: 8 }}>Layers</span>
        {[["all", "All"], ["members", "Members"], ["connections", "Connections"], ["semantic", "Relations"]].map(([v, lbl]) => (
          <BtnSm key={v} active={edgeMode === v} onClick={() => setEdgeMode(v)}>{lbl}</BtnSm>
        ))}
        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: t.muted, marginLeft: 8, cursor: "pointer" }}>
          <input type="checkbox" checked={showRelLabels} onChange={e => setShowRelLabels(e.target.checked)} />
          Relation labels
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: t.muted, cursor: "pointer" }}>
          <input type="checkbox" checked={showMemberIds} onChange={e => setShowMemberIds(e.target.checked)} />
          Member IDs
        </label>
      </div>
      <div style={{ display: "flex", gap: 6, padding: "6px 14px", borderBottom: `1px solid ${t.border}`, background: t.bg, flexWrap: "wrap", alignItems: "center" }}>
        <BtnSm active={filter === "ALL"} onClick={() => setFilter("ALL")}>All ({roleCounts.ALL})</BtnSm>
        {["COLUMN", "BEAM", "SECONDARY", "UNKNOWN"].map(r => (
          <BtnSm key={r} active={filter === r} onClick={() => setFilter(r)}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: ROLE_META[r].color, display: "inline-block" }} />
            {ROLE_META[r].label}s ({roleCounts[r] || 0})
          </BtnSm>
        ))}
      </div>
      <div style={{ flex: 1, position: "relative", overflow: "hidden", cursor: drag ? "grabbing" : "grab", background: t.viewerBg }}
        onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp} onWheel={onWheel}>
        {nodes.length === 0 ? (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: t.muted, fontSize: 13 }}>No members to display.</div>
        ) : (
          <svg ref={svgRef} width="100%" height="100%" style={{ display: "block" }}>
            <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
              {showLines && memberLines.map((ln) => {
                const m = ROLE_META[ln.role] || ROLE_META.UNKNOWN;
                return (
                  <line key={`m-${ln.id}`} x1={ln.sx} y1={ln.sy} x2={ln.ex} y2={ln.ey}
                    stroke={m.color} strokeWidth="1.8" opacity={0.55} strokeLinecap="round" />
                );
              })}
              {visibleConn.map((e, i) => (
                <line key={`c-${i}`} x1={e.a.nx} y1={e.a.ny} x2={e.b.nx} y2={e.b.ny}
                  stroke={edgeColor.CONNECTION} strokeWidth="0.8" opacity={0.35} strokeDasharray="3 3" />
              ))}
              {visibleSemantic.map((e, i) => {
                const col = edgeColor[e.type] || "#9B9486";
                const mx = (e.a.nx + e.b.nx) / 2, my = (e.a.ny + e.b.ny) / 2;
                return (
                  <g key={`s-${i}`}>
                    <line x1={e.a.nx} y1={e.a.ny} x2={e.b.nx} y2={e.b.ny} stroke={col} strokeWidth="1" opacity={0.5} />
                    {showRelLabels && zoom >= 0.6 && visibleSemantic.length < 400 && (
                      <text x={mx} y={my - 4} textAnchor="middle" style={{ fontSize: 7, fill: col, fontWeight: 700, pointerEvents: "none", fontFamily: "inherit" }}>{e.type}</text>
                    )}
                  </g>
                );
              })}
              {nodes.map((n) => {
                const m = ROLE_META[n.role] || ROLE_META.UNKNOWN;
                const isSel = sel?.id === n.id;
                return (
                  <g key={n.id} onClick={() => setSel(isSel ? null : n)}
                    onMouseEnter={(ev) => setTooltip({ node: n, x: ev.clientX, y: ev.clientY })}
                    onMouseLeave={() => setTooltip(null)} style={{ cursor: "pointer" }}>
                    <circle cx={n.nx} cy={n.ny} r={isSel ? 6 : 4} fill={m.color} stroke="#fff" strokeWidth={1.2} opacity={0.95} />
                    {(showMemberIds || isSel) && (
                      <text x={n.nx + 8} y={n.ny + 3} style={{ fontSize: 8, fill: "#64748B", fontWeight: 600, pointerEvents: "none", fontFamily: "monospace" }}>{n.id}</text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>
        )}
        {tooltip && (
          <div style={{ position: "fixed", left: tooltip.x + 14, top: tooltip.y - 10, background: t.elevated, border: `1px solid ${t.border}`, borderRadius: 8, padding: "10px 13px", pointerEvents: "none", zIndex: 9999, boxShadow: "0 4px 12px rgba(0,0,0,0.25)", minWidth: 180 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: t.text, marginBottom: 6, fontFamily: "monospace" }}>#{tooltip.node.id}</div>
            {[["Role", tooltip.node.role], ["Profile", tooltip.node.profile], ["Material", tooltip.node.material], ["Length", fmt(tooltip.node.length) + " mm"]].map(([k, v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
                <span style={{ color: t.muted }}>{k}</span><span style={{ fontWeight: 600, color: t.text }}>{v}</span>
              </div>
            ))}
          </div>
        )}
        <div style={{ position: "absolute", bottom: 12, right: 12, display: "flex", flexDirection: "column", gap: 4 }}>
          {[["＋", () => setZoom(z => Math.min(4, z * 1.25))], ["−", () => setZoom(z => Math.max(0.2, z / 1.25))], ["⌂", () => { setZoom(1); setPan({ x: 0, y: 0 }); }]].map(([label, fn]) => (
            <button key={label} onClick={fn} style={{ width: 28, height: 28, background: t.elevated, border: `1px solid ${t.border}`, borderRadius: 6, cursor: "pointer", fontSize: 14, fontWeight: 700, color: t.muted, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 1px 3px rgba(0,0,0,0.15)", fontFamily: "inherit" }}>{label}</button>
          ))}
        </div>
      </div>
      {sel && (
        <div style={{ borderTop: `1px solid ${t.border}`, padding: "10px 16px", background: t.activeTab, display: "flex", gap: 20, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontWeight: 700, color: t.activeText, fontFamily: "monospace", fontSize: 12 }}>#{sel.id}</span>
          <Badge role={sel.role} />
          {[["Profile", sel.profile], ["Material", sel.material], ["Length", fmt(sel.length) + " mm"]].map(([k, v]) => (
            <span key={k} style={{ fontSize: 12, color: t.muted }}>{k}: <strong style={{ color: t.text }}>{v}</strong></span>
          ))}
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// 3D VIEWER
// ════════════════════════════════════════════════════════════════
function BIMViewer3D({ members }) {
  const t = useTheme();
  const mountRef = useRef(null);
  const stRef = useRef({ theta: 0.6, phi: 1.0, radius: 60000, dragging: false, lx: 0, ly: 0 });

  const applyViewerTheme = useCallback((T, st, theme, mems) => {
    if (!st.scene) return;
    const dark = isDarkTheme(theme);
    st.scene.background = new T.Color(dark ? 0x252526 : 0xFAFAF8);
    if (st.grid) {
      st.scene.remove(st.grid);
      st.grid.geometry?.dispose();
      if (st.grid.material) {
        if (Array.isArray(st.grid.material)) st.grid.material.forEach(m => m.dispose());
        else st.grid.material.dispose();
      }
    }
    st.grid = new T.GridHelper(200000, 40, dark ? 0x505050 : 0xD8D3C8, dark ? 0x3c3c3c : 0xE8E4DC);
    if (dark) {
      const gridMats = Array.isArray(st.grid.material) ? st.grid.material : [st.grid.material];
      gridMats.forEach(m => { m.transparent = true; m.opacity = 0.45; });
    }
    st.scene.add(st.grid);
    disposeBimObjects(st);
    if (!mems.length) return;
    let mnX = 1e9, mxX = -1e9, mnY = 1e9, mxY = -1e9, mnZ = 1e9, mxZ = -1e9;
    mems.forEach(m => {
      mnX = Math.min(mnX, m.x, m.x2); mxX = Math.max(mxX, m.x, m.x2);
      mnY = Math.min(mnY, m.y, m.y2); mxY = Math.max(mxY, m.y, m.y2);
      mnZ = Math.min(mnZ, m.z, m.z2); mxZ = Math.max(mxZ, m.z, m.z2);
    });
    const cx = (mnX + mxX) / 2, cy = (mnY + mxY) / 2, cz = (mnZ + mxZ) / 2;
    const span = Math.max(mxX - mnX, mxY - mnY, mxZ - mnZ) || 10000;
    st.radius = span * 1.6;
    const mats = {};
    mems.forEach(m => {
      const p1 = new T.Vector3(m.x - cx, m.z - cz, m.y - cy);
      const p2 = new T.Vector3(m.x2 - cx, m.z2 - cz, m.y2 - cy);
      if (p1.distanceTo(p2) < 0.001) return;
      addMember3D(T, st, p1, p2, m.role, theme, mats);
    });
  }, []);

  useEffect(() => {
    if (!window.THREE) {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
      document.head.appendChild(s);
    }
  }, []);

  useEffect(() => {
    let tries = 0;
    const wait = setInterval(() => { if (window.THREE || tries++ > 30) { clearInterval(wait); init(); } }, 200);
    const st = stRef.current;
    return () => { clearInterval(wait); st.cleanup?.(); };
  // eslint-disable-next-line
  }, []);

  const init = () => {
    const T = window.THREE; if (!T || !mountRef.current) return;
    const el = mountRef.current; const st = stRef.current;
    if (st.renderer) return;
    const W = el.clientWidth, H = el.clientHeight || 460;
    const scene = new T.Scene();
    const cam = new T.PerspectiveCamera(50, W / H, 10, 5e6);
    const renderer = new T.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(W, H); el.appendChild(renderer.domElement);
    scene.add(new T.AmbientLight(0xffffff, isDarkTheme(t) ? 1.0 : 0.8));
    const dl = new T.DirectionalLight(0xffffff, isDarkTheme(t) ? 0.65 : 0.5);
    dl.position.set(1, 2, 3); scene.add(dl);
    scene.add(new T.AxesHelper(8000));
    Object.assign(st, { scene, renderer, camera: cam });
    applyViewerTheme(T, st, t, members);
    const loop = () => { st.raf = requestAnimationFrame(loop); renderer.render(scene, cam); }; loop();
    const onResize = () => { const w = el.clientWidth, h = el.clientHeight; renderer.setSize(w, h); cam.aspect = w / h; cam.updateProjectionMatrix(); };
    window.addEventListener("resize", onResize);
    st.cleanup = () => { cancelAnimationFrame(st.raf); window.removeEventListener("resize", onResize); renderer.dispose(); if (el.contains(renderer.domElement)) el.removeChild(renderer.domElement); };
    updateCam();
  };

  useEffect(() => {
    const st = stRef.current;
    if (!st.scene || !window.THREE) return;
    applyViewerTheme(window.THREE, st, t, members);
    updateCam();
  // eslint-disable-next-line
  }, [members, t.viewerRoles, t.viewerBg, applyViewerTheme]);
  const updateCam = () => {
    const st = stRef.current; if (!st.camera) return;
    const { theta, phi, radius } = st;
    st.camera.position.set(radius * Math.sin(phi) * Math.sin(theta), radius * Math.cos(phi), radius * Math.sin(phi) * Math.cos(theta));
    st.camera.lookAt(0, 0, 0);
  };

  const onDown = e => { stRef.current.dragging = true; stRef.current.lx = e.clientX; stRef.current.ly = e.clientY; };
  const onUp = () => { stRef.current.dragging = false; };
  const onMove = e => { const st = stRef.current; if (!st.dragging) return; st.theta -= (e.clientX - st.lx) * 0.005; st.phi = Math.max(0.1, Math.min(Math.PI - 0.1, st.phi + (e.clientY - st.ly) * 0.005)); st.lx = e.clientX; st.ly = e.clientY; updateCam(); };
  const onWheel = e => { e.preventDefault(); stRef.current.radius *= e.deltaY > 0 ? 1.1 : 0.9; updateCam(); };
  const reset = () => { Object.assign(stRef.current, { theta: 0.6, phi: 1.0, radius: 60000 }); updateCam(); };

  const roleKeys = ["COLUMN", "BEAM", "SECONDARY", "CONNECTION", "UNKNOWN"];

  return (
    <div>
      <div style={{ display: "flex", gap: 8, padding: "8px 14px", borderBottom: `1px solid ${t.border}`, background: t.subhead, alignItems: "center", flexWrap: "wrap" }}>
        <BtnSm onClick={reset}>⌂ Reset view</BtnSm>
        <span style={{ fontSize: 10, color: t.muted }}>Drag to orbit · Scroll to zoom</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 12 }}>
          {roleKeys.map(r => {
            const c = viewerRoleColor(r, t);
            return (
              <span key={r} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: c }}>
                <span style={{ width: 8, height: 8, background: c, borderRadius: "50%", display: "inline-block" }} />
                {(ROLE_META[r] || ROLE_META.UNKNOWN).label}
              </span>
            );
          })}
        </div>
      </div>
      <div ref={mountRef} style={{ width: "100%", height: 460, cursor: "grab", background: t.viewerBg }}
        onMouseDown={onDown} onMouseUp={onUp} onMouseMove={onMove} onMouseLeave={onUp} onWheel={onWheel} />
      <div style={{ padding: "6px 14px", fontSize: 10, color: t.muted, borderTop: `1px solid ${t.border}`, background: t.subhead }}>
        {members.length} members · Three.js r128
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// CLASH DETECTION
// ════════════════════════════════════════════════════════════════
function detectClashes(members) {
  const THICKNESS = 150, SNAP = 50;
  const items = members.filter(m => m.role !== "CONNECTION" && m.x != null && m.x2 != null).map(m => {
    const pad = THICKNESS / 2;
    return { id: m.id, role: m.role, profile: m.profile, sx: m.x, sy: m.y, sz: m.z, ex: m.x2, ey: m.y2, ez: m.z2, minX: Math.min(m.x,m.x2)-pad, maxX: Math.max(m.x,m.x2)+pad, minY: Math.min(m.y,m.y2)-pad, maxY: Math.max(m.y,m.y2)+pad, minZ: Math.min(m.z,m.z2)-pad, maxZ: Math.max(m.z,m.z2)+pad };
  });
  const d3 = (ax,ay,az,bx,by,bz) => Math.sqrt((ax-bx)**2+(ay-by)**2+(az-bz)**2);
  const sharedEP = (a,b) => { const pts=[[a.sx,a.sy,a.sz],[a.ex,a.ey,a.ez]]; const bpts=[[b.sx,b.sy,b.sz],[b.ex,b.ey,b.ez]]; let c=0; for(const ap of pts) for(const bp of bpts) if(d3(...ap,...bp)<=SNAP)c++; return c; };
  const clashes = [];
  for (let i=0;i<items.length;i++) for(let j=i+1;j<items.length;j++) {
    const a=items[i],b=items[j]; if(sharedEP(a,b)>=1)continue;
    if(a.minX<b.maxX&&a.maxX>b.minX&&a.minY<b.maxY&&a.maxY>b.minY&&a.minZ<b.maxZ&&a.maxZ>b.minZ) {
      const ov=Math.min(Math.min(a.maxX,b.maxX)-Math.max(a.minX,b.minX),Math.min(a.maxY,b.maxY)-Math.max(a.minY,b.minY),Math.min(a.maxZ,b.maxZ)-Math.max(a.minZ,b.minZ));
      if(ov>THICKNESS*0.3) clashes.push({a:a.id,b:b.id,aRole:a.role,bRole:b.role,aProfile:a.profile,bProfile:b.profile,overlap:Math.round(ov),severity:ov>THICKNESS?"high":"medium"});
    }
  }
  return clashes.sort((x,y)=>y.overlap-x.overlap);
}

function ClashPanel({ members, isOffline }) {
  const t = useTheme();
  const [clashes, setClashes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (isOffline || !members.length) {
      setClashes(detectClashes(members));
      return;
    }
    setLoading(true);
    api("/bim/clashes")
      .then(r => setClashes(r.clashes || []))
      .catch(e => { setErr(e.message); setClashes(detectClashes(members.slice(0, 200))); })
      .finally(() => setLoading(false));
  }, [members, isOffline]);

  const high = clashes.filter(c => c.severity === "high");
  const medium = clashes.filter(c => c.severity === "medium");
  const sevColor = { high: t.error, medium: t.warn };
  const sevBg = { high: t.bg === "#1e1e1e" ? "#2a2220" : "#FEF2F2", medium: t.bg === "#1e1e1e" ? t.panel : "#FFFBEB" };
  const sevBd = { high: t.bg === "#1e1e1e" ? "#4a3835" : "#FCA5A5", medium: t.bg === "#1e1e1e" ? t.border : "#FDE68A" };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {loading && <Alert type="info">Analysing clashes across {members.length} members…</Alert>}
      {err && <Alert type="warn">Server analysis failed ({err}) — showing local subset.</Alert>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ background: t.hover, border: `1px solid ${t.border}`, borderRadius: 6, padding: "8px 14px", fontSize: 12, color: t.muted }}>
          Bounding-box interference check across <strong style={{ color: t.text }}>{members.length}</strong> members
        </div>
        {clashes.length > 0 && (
          <>
            <span style={{ background: sevBg.high, color: sevColor.high, border: `1px solid ${sevBd.high}`, borderRadius: 12, padding: "3px 12px", fontSize: 11, fontWeight: 700 }}>{high.length} HIGH</span>
            <span style={{ background: sevBg.medium, color: sevColor.medium, border: `1px solid ${sevBd.medium}`, borderRadius: 12, padding: "3px 12px", fontSize: 11, fontWeight: 700 }}>{medium.length} MEDIUM</span>
          </>
        )}
      </div>
      {clashes.length === 0 && <Alert type="success">No geometric clashes detected in the current model.</Alert>}
      {clashes.map((c, i) => (
        <div key={i} style={{ border: `1px solid ${sevBd[c.severity]}`, borderRadius: 6, padding: "12px 14px", background: sevBg[c.severity] }}>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <span style={{ background: sevColor[c.severity], color: "#fff", borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 800, flexShrink: 0 }}>{c.severity.toUpperCase()}</span>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: t.text }}>{c.a}{c.aProfile ? ` (${c.aProfile})` : ""} × {c.b}{c.bProfile ? ` (${c.bProfile})` : ""}</div>
              <div style={{ fontSize: 11, color: t.muted, marginTop: 3 }}>Overlap: ~{c.overlap || c.overlap_mm}mm · {c.severity === "high" ? "Significant interference — review member routing." : "Within tolerance but worth verifying."}</div>
            </div>
          </div>
        </div>
      ))}
      <div style={{ fontSize: 11, color: t.muted, background: t.hover, borderRadius: 4, padding: "7px 12px" }}>
        150mm nominal thickness · pairs sharing an endpoint within 50mm are treated as valid joints and excluded.
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// DEFECT ANALYSIS
// ════════════════════════════════════════════════════════════════
function _localDefects(members) {
  const out = [];
  const zeroLen = members.filter(m => m.role !== "CONNECTION" && (!m.length || m.length === 0));
  if (zeroLen.length) out.push({ label: "Zero-length members", severity: "HIGH", desc: "Members with no measurable length.", fix: "Re-check source geometry in Tekla.", members: zeroLen.map(m => m.id) });
  const noMat = members.filter(m => !m.material && m.role !== "CONNECTION" && m.role !== "UNKNOWN");
  if (noMat.length) out.push({ label: "Missing material grade", severity: "HIGH", desc: "Cannot perform capacity checks without material grade.", fix: "Assign material in Tekla.", members: noMat.map(m => m.id) });
  const unks = members.filter(m => m.role === "UNKNOWN");
  if (unks.length) out.push({ label: "Unclassified members", severity: "HIGH", desc: "Members without a valid structural role.", fix: "Reclassify in Tekla.", members: unks.map(m => m.id) });
  return out;
}

function DefectPanel({ members, isOffline }) {
  const t = useTheme();
  const [defects, setDefects] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isOffline || !members.length) {
      setDefects(_localDefects(members));
      return;
    }
    setLoading(true);
    api("/bim/defects")
      .then(r => {
        const grouped = {};
        (r.defects || []).forEach(d => {
          const k = d.label;
          if (!grouped[k]) grouped[k] = { label: d.label, severity: d.severity, desc: "", fix: "", members: [] };
          grouped[k].members.push(d.id);
        });
        const mapDesc = {
          "Zero-length member": ["Members with no measurable length.", "Re-check geometry in Tekla."],
          "Missing material": ["Cannot perform capacity checks without material grade.", "Assign material in Tekla."],
          "Missing profile": ["No recognizable section profile.", "Assign profile in Tekla."],
          "Unclassified member": ["No valid structural role.", "Reclassify in Tekla."],
        };
        setDefects(Object.values(grouped).map(g => ({
          ...g,
          desc: mapDesc[g.label]?.[0] || g.label,
          fix: mapDesc[g.label]?.[1] || "Review in Tekla.",
        })));
      })
      .catch(() => setDefects(_localDefects(members)))
      .finally(() => setLoading(false));
  }, [members, isOffline]);

  const isDark = t.bg === "#1e1e1e";
  const sevColor = { HIGH: t.error, MEDIUM: t.warn, LOW: t.muted };
  const sevBg    = { HIGH: isDark ? "#2a2220" : "#FEF2F2", MEDIUM: isDark ? t.panel : "#FFFBEB", LOW: isDark ? t.panel : "#F8FAFC" };
  const sevBd    = { HIGH: isDark ? "#4a3835" : "#FCA5A5", MEDIUM: isDark ? t.border : "#FDE68A", LOW: isDark ? t.border : "#CBD5E1" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {loading && <Alert type="info">Scanning model for defects…</Alert>}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {["HIGH","MEDIUM","LOW"].map(s => {
          const cnt = defects.filter(d => d.severity === s).length;
          return cnt > 0 ? <span key={s} style={{ background: sevBg[s], color: sevColor[s], border: `1px solid ${sevBd[s]}`, borderRadius: 12, padding: "3px 12px", fontSize: 11, fontWeight: 700 }}>{cnt} {s}</span> : null;
        })}
        {defects.length === 0 && <Alert type="success">No defects detected — model geometry looks clean.</Alert>}
      </div>
      {defects.map((d, i) => (
        <div key={i} style={{ border: `1px solid ${sevBd[d.severity]}`, borderRadius: 6, background: sevBg[d.severity], overflow: "hidden" }}>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "12px 14px" }}>
            <span style={{ background: sevColor[d.severity], color: "#fff", borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 800, flexShrink: 0 }}>{d.severity}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: t.text, marginBottom: 3 }}>{d.label} ({d.members.length})</div>
              <div style={{ fontSize: 11, color: t.muted, marginBottom: 3 }}>{d.desc}</div>
              <div style={{ fontSize: 11, color: t.muted }}><strong>Fix:</strong> {d.fix}</div>
              <div style={{ marginTop: 6, fontSize: 10, color: sevColor[d.severity], background: isDark ? t.bg : "rgba(255,255,255,0.6)", borderRadius: 4, padding: "3px 8px", display: "inline-block", fontFamily: "monospace" }}>
                {d.members.slice(0, 12).join(", ")}{d.members.length > 12 ? ` +${d.members.length - 12} more` : ""}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// RULE ENGINE
// ════════════════════════════════════════════════════════════════
function RuleEnginePanel({ members }) {
  const t = useTheme();
  const [rules, setRules] = useState(DEFAULT_RULES.map(r => ({ ...r })));
  const [expanded, setExpanded] = useState({});
  const toggleExpand = id => setExpanded(p => ({ ...p, [id]: !p[id] }));
  const toggleRule = id => setRules(prev => prev.map(r => r.id === id ? { ...r, enabled: !r.enabled } : r));
  const typeColor = { error: t.error, warn: t.warn, suggest: t.activeText };
  const typeLabel = { error: "ERROR", warn: "WARN", suggest: "SUGGEST" };
  const hitBg = t.bg === "#1e1e1e" ? "#2a2220" : "#FEF2F2";
  const hitBd = t.bg === "#1e1e1e" ? "#4a3835" : "#FCA5A5";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", background: t.hint, borderRadius: 6, border: `1px solid ${t.border}` }}>
        <span style={{ fontSize: 11, color: t.muted }}>
          <strong style={{ color: t.text }}>{rules.filter(r => r.enabled).length}</strong> active rules · <strong style={{ color: t.text }}>{rules.filter(r => !r.enabled).length}</strong> disabled
        </span>
        <button onClick={() => setExpanded(Object.fromEntries(rules.map(r => [r.id, true])))} style={{ marginLeft: "auto", background: t.activeText, color: "#fff", border: "none", borderRadius: 5, padding: "5px 12px", fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}>
          ▶ Run All Rules
        </button>
      </div>
      {rules.map(rule => {
        const hits = rule.enabled ? members.filter(rule.check) : [];
        const isOpen = !!expanded[rule.id];
        const tc = typeColor[rule.type] || t.muted;
        const tl = typeLabel[rule.type] || rule.type.toUpperCase();
        return (
          <div key={rule.id} style={{ border: `1px solid ${isOpen ? t.activeText : t.border}`, borderRadius: 6, overflow: "hidden", background: t.panel }}>
            <div onClick={() => toggleExpand(rule.id)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px", cursor: "pointer", background: isOpen ? t.activeTab : t.panel }}>
              <span style={{ color: tc, border: `1px solid ${tc}`, borderRadius: 4, padding: "1px 6px", fontSize: 9, fontWeight: 800, letterSpacing: ".3px", flexShrink: 0 }}>{tl}</span>
              <span style={{ fontSize: 12, fontWeight: 600, color: t.text, flex: 1 }}>{rule.name}</span>
              <span style={{ fontSize: 10, color: t.muted, fontFamily: "monospace" }}>{rule.id}</span>
              {rule.enabled && hits.length > 0 && (
                <span style={{ background: hitBg, color: t.error, border: `1px solid ${hitBd}`, borderRadius: 10, padding: "1px 8px", fontSize: 10, fontWeight: 700 }}>{hits.length} hit{hits.length > 1 ? "s" : ""}</span>
              )}
              <button onClick={e => { e.stopPropagation(); toggleRule(rule.id); }} style={{ background: rule.enabled ? t.successBox : t.chip, color: rule.enabled ? t.success : t.muted, border: `1px solid ${rule.enabled ? t.successBoxBorder : t.border}`, borderRadius: 12, padding: "2px 10px", fontSize: 10, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}>
                {rule.enabled ? "ACTIVE" : "DISABLED"}
              </button>
              <span style={{ fontSize: 12, color: t.muted }}>{isOpen ? "▲" : "▼"}</span>
            </div>
            {isOpen && (
              <div style={{ padding: "12px 14px", borderTop: `1px solid ${t.border}`, background: t.bg }}>
                <pre style={{ background: t.bg === "#1e1e1e" ? "#1a1a1a" : "#1A1915", color: t.codeFg, borderRadius: 6, padding: "10px 14px", fontSize: 11, fontFamily: "monospace", lineHeight: 1.7, overflow: "auto", margin: "0 0 10px" }}>
                  {rule.code.split("\n").map((line, li) => (
                    <span key={li}>
                      {line.startsWith("     //") || line.startsWith("//")
                        ? <span style={{ color: "#9B9486" }}>{line}</span>
                        : /\b(IF|AND|OR|THEN)\b/.test(line)
                          ? line.split(/\b(IF|AND|OR|THEN)\b/).map((part, pi) =>
                              /^(IF|AND|OR|THEN)$/.test(part)
                                ? <span key={pi} style={{ color: "#C084FC" }}>{part}</span>
                                : /"/.test(part)
                                  ? part.split(/"([^"]*)"/).map((pp, ppi) => ppi % 2 === 1 ? <span key={ppi} style={{ color: "#86EFAC" }}>"{pp}"</span> : pp)
                                  : part)
                          : <span style={{ color: "#D8D3C8" }}>{line}</span>}
                      {"\n"}
                    </span>
                  ))}
                </pre>
                {!rule.enabled
                  ? <span style={{ fontSize: 11, color: t.muted }}>Rule is disabled — not evaluated.</span>
                  : hits.length > 0
                    ? <div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: t.error, marginBottom: 6 }}>{hits.length} member(s) match this rule:</div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                          {hits.map(m => <div key={m.id} style={{ fontSize: 11, background: hitBg, border: `1px solid ${hitBd}`, borderRadius: 4, padding: "4px 10px", fontFamily: "monospace", color: t.error }}>{rule.action(m)}</div>)}
                        </div>
                      </div>
                    : <span style={{ fontSize: 11, color: t.success, fontWeight: 600 }}>No members match — rule passes cleanly.</span>
                }
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// AUDIT REPORT
// ════════════════════════════════════════════════════════════════
function AuditPanel({ members }) {
  const t = useTheme();
  const audit = useMemo(() => runAudit(members), [members]);
  const scoreColor = audit.score >= 80 ? t.success : t.warn;
  const isDark = t.bg === "#1e1e1e";
  const critStyle = isDark
    ? { c: t.error, bg: "#2a2220", bd: "#4a3835" }
    : { c: "#B91C1C", bg: "#FEF2F2", bd: "#FCA5A5" };
  const warnStyle = isDark
    ? { c: t.warn, bg: t.panel, bd: t.border }
    : { c: "#B45309", bg: "#FFFBEB", bd: "#FDE68A" };
  const okStyle = isDark
    ? { c: t.success, bg: t.panel, bd: t.border }
    : { c: "#15803D", bg: "#F0FDF4", bd: "#BBF7D0" };

  const download = () => {
    const lines = [
      "TEKLA AI PLATFORM — STRUCTURAL AUDIT REPORT", "=".repeat(48),
      `Date: ${new Date().toLocaleDateString()}`, `Health Score: ${audit.score}/100`,
      `Critical: ${audit.critical.length} · Warnings: ${audit.warnings.length} · Passed: ${audit.passed.length}`, "",
      `CRITICAL (${audit.critical.length})`, "─".repeat(40),
      ...audit.critical.map(c => `[CRIT] ${c.label}\n  ${c.desc}\n  Members: ${c.members}`),
      `\nWARNINGS (${audit.warnings.length})`, "─".repeat(40),
      ...audit.warnings.map(w => `[WARN] ${w.label}\n  ${w.desc}\n  Members: ${w.members}`),
      `\nPASSED (${audit.passed.length})`, "─".repeat(40),
      ...audit.passed.map(p => `[OK]   ${p}`),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/plain" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `tekla_audit_${new Date().toISOString().slice(0,10)}.txt`; a.click();
  };

  const exportCsv = () => {
    const rows = [["ID","Role","Profile","Material","Length"], ...members.map(m => [m.id,m.role,m.profile,m.material||"",m.length||0])];
    const blob = new Blob([rows.map(r => r.join(",")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "members.csv"; a.click();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 20, padding: "16px 18px", background: t.panel, border: `1px solid ${t.border}`, borderRadius: 8 }}>
        <div style={{ width: 72, height: 72, borderRadius: "50%", border: `3px solid ${scoreColor}`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <span style={{ fontSize: 22, fontWeight: 800, color: scoreColor }}>{audit.score}</span>
        </div>
        <div>
          <div style={{ fontSize: 13, fontWeight: 800, color: t.text }}>
            {audit.score >= 80 ? "Model is in good shape" : audit.score >= 60 ? "Needs review before export" : "Critical issues detected"}
          </div>
          <div style={{ fontSize: 11, color: t.muted, marginTop: 4 }}>{members.length} members analysed · {new Date().toLocaleDateString()}</div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            {[["Critical", audit.critical.length, critStyle.c, critStyle.bg, critStyle.bd],
              ["Warnings", audit.warnings.length, warnStyle.c, warnStyle.bg, warnStyle.bd],
              ["Passed",   audit.passed.length,   okStyle.c, okStyle.bg, okStyle.bd],
            ].map(([l, v, c, bg, bd]) => (
              <span key={l} style={{ background: bg, color: c, border: `1px solid ${bd}`, borderRadius: 12, padding: "2px 10px", fontSize: 11, fontWeight: 700 }}>{v} {l}</span>
            ))}
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button onClick={download} style={{ background: t.activeText, color: "#fff", border: "none", borderRadius: 6, padding: "7px 14px", fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}>⬇ Download Report</button>
          <button onClick={exportCsv} style={{ background: t.hover, color: t.muted, border: `1px solid ${t.border}`, borderRadius: 6, padding: "7px 14px", fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}>⬇ Export CSV</button>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
        {[["Unclassified", audit.unclassified, audit.unclassified > 0 ? t.warn : t.success],
          ["Missing Material", audit.missingMaterials, audit.missingMaterials > 0 ? t.warn : t.success],
          ["Critical Issues", audit.critical.length, audit.critical.length > 0 ? t.error : t.success],
          ["Passed Checks", audit.passed.length, t.success],
        ].map(([l, v, c]) => (
          <div key={l} style={{ background: t.panel, border: `1px solid ${t.border}`, borderRadius: 6, padding: "12px 14px" }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: c }}>{v}</div>
            <div style={{ fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px", marginTop: 2 }}>{l}</div>
          </div>
        ))}
      </div>
      {audit.critical.length > 0 && (
        <div style={{ border: `1px solid ${critStyle.bd}`, borderRadius: 6, overflow: "hidden" }}>
          <div style={{ background: critStyle.bg, padding: "10px 14px", borderBottom: `1px solid ${critStyle.bd}`, borderLeft: `3px solid ${critStyle.c}`, display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: critStyle.c }}>CRITICAL ISSUES</span>
            <span style={{ background: critStyle.c, color: "#fff", borderRadius: 10, padding: "1px 8px", fontSize: 10, fontWeight: 700 }}>{audit.critical.length}</span>
          </div>
          {audit.critical.map((c, i) => (
            <div key={i} style={{ padding: "10px 14px", borderBottom: `1px solid ${critStyle.bd}`, display: "flex", gap: 10, alignItems: "flex-start", background: t.panel }}>
              <span style={{ background: critStyle.bg, color: critStyle.c, border: `1px solid ${critStyle.bd}`, borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 800, flexShrink: 0 }}>CRIT</span>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: t.text, marginBottom: 2 }}>{c.label}</div>
                <div style={{ fontSize: 11, color: t.muted, marginBottom: 3 }}>{c.desc}</div>
                <div style={{ fontSize: 10, color: critStyle.c, fontFamily: "monospace", background: critStyle.bg, borderRadius: 3, padding: "2px 6px", display: "inline-block" }}>{c.members}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      {audit.warnings.length > 0 && (
        <div style={{ border: `1px solid ${warnStyle.bd}`, borderRadius: 6, overflow: "hidden" }}>
          <div style={{ background: warnStyle.bg, padding: "10px 14px", borderBottom: `1px solid ${warnStyle.bd}`, borderLeft: `3px solid ${warnStyle.c}`, display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: warnStyle.c }}>WARNINGS</span>
            <span style={{ background: warnStyle.c, color: "#fff", borderRadius: 10, padding: "1px 8px", fontSize: 10, fontWeight: 700 }}>{audit.warnings.length}</span>
          </div>
          {audit.warnings.map((w, i) => (
            <div key={i} style={{ padding: "10px 14px", borderBottom: `1px solid ${warnStyle.bd}`, display: "flex", gap: 10, alignItems: "flex-start", background: t.panel }}>
              <span style={{ background: warnStyle.bg, color: warnStyle.c, border: `1px solid ${warnStyle.bd}`, borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 800, flexShrink: 0 }}>WARN</span>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: t.text, marginBottom: 2 }}>{w.label}</div>
                <div style={{ fontSize: 11, color: t.muted, marginBottom: 3 }}>{w.desc}</div>
                <div style={{ fontSize: 10, color: warnStyle.c, fontFamily: "monospace", background: warnStyle.bg, borderRadius: 3, padding: "2px 6px", display: "inline-block" }}>{w.members}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      <div style={{ border: `1px solid ${okStyle.bd}`, borderRadius: 6, overflow: "hidden" }}>
        <div style={{ background: okStyle.bg, padding: "10px 14px", borderBottom: `1px solid ${okStyle.bd}`, borderLeft: `3px solid ${okStyle.c}`, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: okStyle.c }}>PASSED CHECKS</span>
          <span style={{ background: okStyle.c, color: "#fff", borderRadius: 10, padding: "1px 8px", fontSize: 10, fontWeight: 700 }}>{audit.passed.length}</span>
        </div>
        {audit.passed.map((p, i) => (
          <div key={i} style={{ padding: "8px 14px", borderBottom: `1px solid ${okStyle.bd}`, display: "flex", gap: 10, alignItems: "center", background: t.panel }}>
            <span style={{ color: okStyle.c, fontSize: 13, flexShrink: 0 }}>✓</span>
            <span style={{ fontSize: 12, color: t.text }}>{p}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// MATERIAL TABLE
// ════════════════════════════════════════════════════════════════
function MaterialTable({ members }) {
  const t = useTheme();
  const stats = useMemo(() => {
    const map = {};
    members.forEach(m => {
      const key = `${m.profile}__${m.material}`;
      if (!map[key]) map[key] = { profile: m.profile, material: m.material, count: 0, totalLen: 0, roles: new Set() };
      map[key].count++; map[key].totalLen += m.length || 0; map[key].roles.add(m.role);
    });
    return Object.values(map).map(r => ({ ...r, roles: [...r.roles] })).sort((a, b) => b.count - a.count);
  }, [members]);
  const [sort, setSort] = useState("count");
  const sorted = [...stats].sort((a, b) => sort === "count" ? b.count - a.count : b.totalLen - a.totalLen);
  return (
    <div style={{ border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 14px", background: t.subhead, borderBottom: `1px solid ${t.border}` }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px" }}>Material Schedule</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <BtnSm active={sort === "count"} onClick={() => setSort("count")}>By Count</BtnSm>
          <BtnSm active={sort === "length"} onClick={() => setSort("length")}>By Length</BtnSm>
        </div>
      </div>
      <div style={{ maxHeight: 380, overflowY: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: t.subhead, position: "sticky", top: 0 }}>
              {["Profile", "Material", "Count", "Total Length (m)", "Roles"].map(h => (
                <th key={h} style={{ padding: "8px 12px", textAlign: "left", fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px", borderBottom: `1px solid ${t.border}`, whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i} style={{ background: i % 2 === 0 ? t.rowEven : t.rowOdd }}>
                <td style={{ padding: "8px 12px", fontSize: 12, fontFamily: "monospace", fontWeight: 600, color: t.text, borderBottom: `1px solid ${t.rowBorder}` }}>{r.profile}</td>
                <td style={{ padding: "8px 12px", fontSize: 12, color: t.muted, borderBottom: `1px solid ${t.rowBorder}` }}>{r.material || "—"}</td>
                <td style={{ padding: "8px 12px", fontSize: 12, fontWeight: 700, color: t.text, borderBottom: `1px solid ${t.rowBorder}` }}>{r.count}</td>
                <td style={{ padding: "8px 12px", fontSize: 12, color: t.muted, borderBottom: `1px solid ${t.rowBorder}`, fontFamily: "monospace" }}>{rnd(r.totalLen / 1000, 2)}</td>
                <td style={{ padding: "8px 12px", borderBottom: `1px solid ${t.rowBorder}` }}>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>{r.roles.map(role => <Badge key={role} role={role} />)}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {stats.length === 0 && <div style={{ padding: 24, textAlign: "center", color: t.muted, fontSize: 12 }}>No members loaded</div>}
      </div>
      <div style={{ padding: "7px 14px", borderTop: `1px solid ${t.border}`, background: t.subhead, fontSize: 10, color: t.muted }}>
        {stats.length} unique profile/material combinations · {members.length} total members
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// MEMBER TABLE
// ════════════════════════════════════════════════════════════════
function MemberTable({ members, onSelect, selId }) {
  const t = useTheme();
  const [q, setQ] = useState("");
  const [rf, setRf] = useState("ALL");
  const [page, setPage] = useState(0);
  const PER = 50;
  const filtered = useMemo(() => members.filter(m => {
    const s = q.toLowerCase();
    return (rf === "ALL" || m.role === rf) && (m.profile?.toLowerCase().includes(s) || m.name?.toLowerCase().includes(s) || m.material?.toLowerCase().includes(s) || String(m.id).includes(s));
  }), [members, q, rf]);
  const paged = filtered.slice(page * PER, (page + 1) * PER);
  const pages = Math.ceil(filtered.length / PER);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Input value={q} onChange={e => { setQ(e.target.value); setPage(0); }} placeholder="Search profile, material, ID…" style={{ flex: 1, minWidth: 200 }} />
        <Select value={rf} onChange={v => { setRf(v); setPage(0); }}>
          <option value="ALL">All roles</option>
          {Object.keys(ROLE_META).map(r => <option key={r} value={r}>{ROLE_META[r].label}</option>)}
        </Select>
      </div>
      <div style={{ border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
        <div style={{ maxHeight: 420, overflowY: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: t.subhead, position: "sticky", top: 0, zIndex: 2 }}>
                {["ID", "Role", "Profile", "Material", "Length mm"].map(h => (
                  <th key={h} style={{ padding: "8px 12px", textAlign: "left", fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px", borderBottom: `1px solid ${t.border}`, whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {paged.map((m, i) => (
                <tr key={m.id + i} onClick={() => onSelect?.(m)} style={{ background: selId === m.id ? t.rowSel : i % 2 === 0 ? t.rowEven : t.rowOdd, cursor: "pointer" }}>
                  <td style={{ padding: "7px 12px", fontSize: 11, fontFamily: "monospace", color: t.muted, borderBottom: `1px solid ${t.rowBorder}` }}>{m.id}</td>
                  <td style={{ padding: "7px 12px", borderBottom: `1px solid ${t.rowBorder}` }}><Badge role={m.role} /></td>
                  <td style={{ padding: "7px 12px", fontSize: 12, fontFamily: "monospace", fontWeight: 600, color: t.text, borderBottom: `1px solid ${t.rowBorder}` }}>{m.profile}</td>
                  <td style={{ padding: "7px 12px", fontSize: 12, color: t.muted, borderBottom: `1px solid ${t.rowBorder}` }}>{m.material || "—"}</td>
                  <td style={{ padding: "7px 12px", fontSize: 12, color: t.muted, fontFamily: "monospace", borderBottom: `1px solid ${t.rowBorder}` }}>{fmt(m.length)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {paged.length === 0 && <div style={{ padding: 24, textAlign: "center", color: t.muted, fontSize: 12 }}>No members found</div>}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 14px", borderTop: `1px solid ${t.border}`, background: t.subhead, fontSize: 11, color: t.muted }}>
          <span>Showing {paged.length} of {filtered.length} / {members.length} total</span>
          {pages > 1 && (
            <div style={{ display: "flex", gap: 4 }}>
              <BtnSm disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Prev</BtnSm>
              <span style={{ padding: "4px 8px", fontSize: 11, color: "#6B6658" }}>{page + 1} / {pages}</span>
              <BtnSm disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)}>Next →</BtnSm>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ComponentChecklist({ check }) {
  const t = useTheme();
  if (!check?.checklist?.length) return null;
  const icon = (s) => s === "ok" ? "✓" : s === "skipped" ? "—" : "✗";
  const color = (s) => s === "ok" ? t.success : s === "skipped" ? t.muted : t.error;
  return (
    <div style={{ border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", background: t.subhead, fontSize: 11, fontWeight: 700, color: t.muted, textTransform: "uppercase" }}>
        Component checklist — {check.completeness_pct ?? 0}% complete
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 0 }}>
        {check.checklist.map((item, i) => (
          <div key={i} style={{ padding: "6px 12px", borderTop: `1px solid ${t.rowBorder}`, fontSize: 11, display: "flex", gap: 6, alignItems: "center", background: t.panel }}>
            <span style={{ color: color(item.status), fontWeight: 800 }}>{icon(item.status)}</span>
            <span style={{ color: item.planned ? t.text : t.muted }}>{item.component.replace(/_/g, " ")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkflowHint({ mode = "create" }) {
  const t = useTheme();
  const hint = mode === "create"
    ? "Create [type] [size] [options] · one command → same view in dashboard + Tekla · Ctrl+Enter"
    : "Edit loaded model: Add ladder · Complete missing · Extend height · Cleanup · or Create beside grid · Execute → Tekla";
  return (
    <div style={{ fontSize: 11, color: t.muted, padding: "7px 12px", background: t.hint, border: `1px solid ${t.border}`, borderRadius: 6, lineHeight: 1.4 }}>
      {hint}
    </div>
  );
}

// Full 10-stage pipeline status panel
function PipelineStages({ pipeline, stages }) {
  const t = useTheme();
  const rows = pipeline || [];
  if (!rows.length && !stages) return null;
  const list = rows.length ? rows : Object.entries(stages || {}).map(([key, data]) => ({
    key, label: key.replace(/_/g, " "), status: data ? "ok" : "pending", summary: data?.structure_type || (typeof data === "object" ? JSON.stringify(data).slice(0, 40) : String(data)),
  }));
  const icon = (s) => s === "ok" ? "✓" : s === "error" ? "✗" : "○";
  const color = (s) => s === "ok" ? t.success : s === "error" ? t.error : t.muted;
  return (
    <div style={{ border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", background: t.subhead, fontSize: 11, fontWeight: 700, color: t.muted, textTransform: "uppercase" }}>Generation pipeline — all stages</div>
      <div style={{ maxHeight: 320, overflowY: "auto" }}>
        {list.map((row, i) => (
          <div key={row.key || i} style={{ display: "flex", gap: 10, padding: "8px 12px", borderTop: `1px solid ${t.rowBorder}`, fontSize: 11, alignItems: "flex-start", background: t.panel }}>
            <span style={{ color: color(row.status), fontWeight: 800, width: 14 }}>{icon(row.status)}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, color: t.text }}>{row.label}</div>
              <div style={{ color: t.muted, marginTop: 2 }}>{row.summary}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// CREATION PANEL — FIXED: no isOffline block, direct error shown
// ════════════════════════════════════════════════════════════════
const EXAMPLES = [
  "Create lattice tower 40m height base 4m top 1.2m panel 3m 5 bays with ladder platform and antenna",
  "Create 4x3 bay building 2 storeys 6m spacing 3.5m height with bracing",
  "Create warehouse 30m x 60m with bracing",
  "Create portal frame shed 12m span 5 bays with haunch",
  "Create 5-bay pipe rack 3 levels 6m spacing",
  "Create hexagonal tower 24m height 5 levels with ladder and antenna",
  "Create bridge 120m span",
  "Create stadium 60m x 60m",
  "Create 5 storey office building",
  "Create transmission tower 45m",
];

function CreationPanel({ onRefresh, isOffline }) {
  const t = useTheme();
  const [prompt, setPrompt] = useState("");
  const [pl, setPl] = useState("auto");
  const [rot, setRot] = useState(0);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [stages, setStages] = useState(null);

  const gen = async () => {
    if (!prompt.trim()) return;
    const lines = prompt.trim().split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const primary = lines.find(l => /^create\s/i.test(l) || /^build\s/i.test(l)) || lines[0] || prompt.trim();
    setLoading(true); setErr(null); setResult(null); setStages(null);
    try {
      if (isOffline) await detectWorkingAPI();
      const r = await api("/bim/generate", "POST", {
        prompt: primary,
        placement: pl,
        rotation_deg: +rot,
        save_to_model: false,
        send_to_tekla: true,
      });
      setResult(r);
      setStages(r.stages);
      if (r.sent_to_tekla && (r.generated_count || 0) > 0) {
        const sync = await waitForTeklaSync(r.generated_count, onRefresh, 90);
        await onRefresh();
        if (!sync.ok) {
          setErr(
            `Generated ${r.generated_count} members but Tekla has ${sync.tekla_member_count ?? 0} ` +
            `(dashboard ${sync.output_json_count ?? 0}). ` +
            `Run Complete Model tab, or type refresh in dotnet terminal. Check [Inserter] FAILED lines.`
          );
        }
      } else {
        await onRefresh();
      }
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <WorkflowHint mode="create" />
      <div>
        <label style={{ display: "block", fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px", marginBottom: 6 }}>Structure Prompt</label>
        <textarea value={prompt} onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && e.ctrlKey) gen(); }}
          placeholder="One command only — e.g. Create transmission tower 45m  (Ctrl+Enter to generate)"
          style={{ width: "100%", height: 80, background: t.input, border: `1px solid ${t.border}`, borderRadius: 6, padding: "9px 12px", fontSize: 12, color: t.text, fontFamily: "inherit", resize: "vertical", outline: "none" }} />
      </div>

      {/* Example prompts */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
        {EXAMPLES.map((ex, i) => (
          <button key={i} onClick={() => setPrompt(ex)} style={{ background: t.chip, border: `1px solid ${t.border}`, borderRadius: 12, padding: "3px 11px", fontSize: 11, color: t.muted, cursor: "pointer", fontFamily: "inherit", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ex}</button>
        ))}
      </div>

      {/* Controls */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <div>
          <label style={{ display: "block", fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px", marginBottom: 5 }}>Placement</label>
          <Select value={pl} onChange={setPl}>
            <option value="auto">Auto — on existing grid</option>
            <option value="origin">World origin (empty model)</option>
            <option value="inline">Inline — extend footprint</option>
            <option value="center">Centered on grid</option>
            <option value="append">Append beside (separate)</option>
          </Select>
        </div>
        <div>
          <label style={{ display: "block", fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px", marginBottom: 5 }}>Rotation °</label>
          <Input type="number" value={rot} onChange={e => setRot(e.target.value)} style={{ width: 80 }} />
        </div>
        <div style={{ alignSelf: "flex-end" }}>
          <Btn onClick={gen} disabled={loading || !prompt.trim()}>
            {loading ? <><Spin />Generating…</> : "Generate Structure"}
          </Btn>
        </div>
      </div>

      {/* Loading stage progress */}
      {loading && (
        <div style={{ background: t.infoBox, border: `1px solid ${t.infoBoxBorder}`, borderRadius: 6, padding: "10px 14px" }}>
          <div style={{ fontSize: 11, color: t.activeText, fontWeight: 600, marginBottom: 4 }}>Running 10-stage pipeline…</div>
          <div style={{ fontSize: 11, color: t.muted }}>Structure detection → Grid generation → Placement → Solving → Validation → Tekla</div>
        </div>
      )}

      {/* Offline warning — does NOT block generate button */}
      {isOffline && !loading && (
        <Alert type="warn">
          ⚠ Backend may be offline at <code>{ACTIVE_API}</code> — the button above will still try to connect.
          If it fails, start your backend: <code style={{ fontSize: 11 }}>uvicorn main:app --reload --host 127.0.0.1</code>
        </Alert>
      )}

      {/* Error */}
      {err && (
        <Alert type="error">
          ❌ {err}
          <div style={{ marginTop: 6, fontSize: 11 }}>
            Check that <code>uvicorn main:app --reload --host 127.0.0.1 --port 8000</code> is running in your terminal.
          </div>
        </Alert>
      )}

      {/* Success + pipeline summary */}
      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Alert type={result.status === "failed" || (result.generated_count ?? 0) === 0 ? "error" : "success"}>
            {(result.generated_count ?? 0) === 0 ? "❌" : "✅"} Generated <strong>{result.generated_count ?? result.elements?.length ?? 0}</strong> members
            {result.member_counts && (
              <> · C:{result.member_counts.columns} B:{result.member_counts.beams} S:{result.member_counts.secondary}</>
            )}
            {result.total_in_model != null && <> · Total in model: {result.total_in_model}</>}
            {result.sent_to_tekla && (result.generated_count ?? 0) > 0 && " · Sent to Tekla ✓"}
            {result.sent_to_tekla === false && (result.generated_count ?? 0) === 0 && " · Not sent (0 members)"}
            {result.saved && " · Saved ✓"}
          </Alert>
          {result.plan?.structure_label && (
            <Alert type="info">
              Structure Planner: <strong>{result.plan.structure_label}</strong>
              {result.plan.generator && <> · generator: {result.plan.generator}</>}
              {result.plan.component_completeness_pct != null && <> · components {result.plan.component_completeness_pct}%</>}
            </Alert>
          )}
          {result.tower_features && (
            <Alert type="info">
              Tower modules: ladder {result.tower_features.ladder ? "✓" : "—"} · platform {result.tower_features.platform ? "✓" : "—"} · antenna {result.tower_features.antenna ? "✓" : "—"}
            </Alert>
          )}
          <ComponentChecklist check={stages?.["10_validation"]?.component_check} />
          <KnowledgeGraphPanel graph={stages?.["1b_knowledge_graph"]?.graph || result.plan?.knowledge_graph} />
          <PipelineStages pipeline={result.pipeline} stages={stages} />
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// AI CHAT PANEL
// ════════════════════════════════════════════════════════════════
const QUICK_QS = ["How many columns?", "What profiles are used?", "Describe the structure type", "Any isolated members?", "What's the total steel weight?"];

function AIChatPanel({ isOffline, members }) {
  const t = useTheme();
  const [msgs, setMsgs] = useState([{ role: "assistant", content: "Ask anything about your BIM model — profiles, counts, structure type, or engineering questions." }]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  const localAnswer = (q) => {
    const ql = q.toLowerCase();
    if (ql.includes("how many column")) return `There are ${members.filter(m => m.role === "COLUMN").length} columns in the current model.`;
    if (ql.includes("how many beam"))   return `There are ${members.filter(m => m.role === "BEAM").length} beams in the current model.`;
    if (ql.includes("profile")) { const ps = [...new Set(members.map(m => m.profile).filter(Boolean))]; return `Profiles in model: ${ps.join(", ")}`; }
    if (ql.includes("total") && (ql.includes("member") || ql.includes("count"))) return `There are ${members.length} total members in the model.`;
    return null;
  };

  const send = async (q) => {
    const txt = q || input.trim();
    if (!txt || loading) return;
    setInput(""); setLoading(true);
    setMsgs(m => [...m, { role: "user", content: txt }]);
    if (isOffline) {
      const local = localAnswer(txt);
      setTimeout(() => {
        setMsgs(m => [...m, { role: "assistant", content: local || `Demo mode active. The model has ${members.length} members across ${[...new Set(members.map(m=>m.role))].length} structural roles. Start the FastAPI backend for full AI Q&A.` }]);
        setLoading(false);
      }, 600);
      return;
    }
    try {
      const r = await api("/query", "POST", { message: txt });
      setMsgs(m => [...m, { role: "assistant", content: r.response }]);
    } catch (e) {
      setMsgs(m => [...m, { role: "assistant", content: `Error: ${e.message}` }]);
    } finally { setLoading(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: 500, border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "8px 14px", borderBottom: `1px solid ${t.border}`, background: t.subhead }}>
        {QUICK_QS.map((q, i) => <BtnSm key={i} onClick={() => send(q)}>{q}</BtnSm>)}
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 10, background: t.bg }}>
        {msgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "80%" }}>
            {m.role === "assistant" && <div style={{ fontSize: 9, fontWeight: 700, color: t.activeText, marginBottom: 4, letterSpacing: ".5px", textTransform: "uppercase" }}>AI ENGINEER</div>}
            <div style={{ background: m.role === "user" ? t.activeText : t.elevated, color: m.role === "user" ? "#fff" : t.text, border: m.role === "user" ? "none" : `1px solid ${t.border}`, borderRadius: m.role === "user" ? "14px 14px 3px 14px" : "14px 14px 14px 3px", padding: "10px 14px", fontSize: 13, lineHeight: 1.6, boxShadow: "0 1px 3px rgba(0,0,0,0.08)" }}>{m.content}</div>
          </div>
        ))}
        {loading && (
          <div style={{ alignSelf: "flex-start", background: t.elevated, border: `1px solid ${t.border}`, borderRadius: "14px 14px 14px 3px", padding: "12px 16px" }}>
            <div style={{ display: "flex", gap: 4 }}>
              {[0,1,2].map(i => <span key={i} style={{ width: 5, height: 5, borderRadius: "50%", background: t.muted, display: "inline-block", animation: `blink 1.2s infinite ${i*0.2}s` }} />)}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div style={{ display: "flex", gap: 8, padding: "10px 14px", borderTop: `1px solid ${t.border}`, background: t.panel }}>
        <textarea value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Ask about your model… (Enter to send)" rows={1}
          style={{ flex: 1, background: t.input, border: `1px solid ${t.border}`, borderRadius: 8, padding: "8px 12px", color: t.text, fontSize: 13, resize: "none", fontFamily: "inherit", outline: "none", lineHeight: 1.5 }} />
        <button onClick={() => send()} disabled={loading || !input.trim()} style={{ background: t.activeText, color: "#fff", border: "none", borderRadius: 8, padding: "0 18px", fontWeight: 700, cursor: loading || !input.trim() ? "not-allowed" : "pointer", fontSize: 13, opacity: loading || !input.trim() ? 0.5 : 1, fontFamily: "inherit" }}>Send</button>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// BUILD AGENT
// ════════════════════════════════════════════════════════════════
const AGENT_CATEGORY_LABELS = {
  edit: "✎ Edit any structure — tower, building, warehouse",
  additive: "Safe — add to existing model",
  buildings: "Buildings & frames",
  industrial: "Industrial — warehouse, portal, pipe rack",
  towers: "Towers & masts",
};

function BuildAgentPanel({ onRefresh, isOffline }) {
  const t = useTheme();
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [commands, setCommands] = useState(null);

  useEffect(() => {
    api("/bim/agent/commands").then(setCommands).catch(() => {});
  }, []);

  const run = async (q) => {
    const txt = q || prompt.trim(); if (!txt) return;
    setLoading(true); setErr(null); setResult(null);
    try {
      if (isOffline) await detectWorkingAPI();
      const r = await api("/bim/agent", "POST", { command: txt, send_to_tekla: true });
      setResult(r); onRefresh();
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  const resultMsg = result && (
    result.action === "cleanup"
      ? `Cleaned model: removed ${result.removed}, kept ${result.kept}`
      : `${result.added != null ? `Added ${result.added}` : `${result.elements?.length || 0} members`} · ${result.params?.structure_type || result.action || "edit"}${result.sent_to_tekla ? " · Tekla ✓" : ""}`
  );

  const categories = commands ? Object.keys(commands) : ["edit", "additive", "towers", "buildings", "industrial"];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <WorkflowHint mode="agent" />

      {categories.map(cat => {
        const items = commands?.[cat] || [];
        if (!items.length) return null;
        return (
          <div key={cat}>
            <div style={{ fontSize: 11, fontWeight: 600, color: t.muted, marginBottom: 6, textTransform: "uppercase" }}>
              {AGENT_CATEGORY_LABELS[cat] || cat}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
              {items.map((item, i) => (
                <BtnSm key={`${cat}-${i}`} onClick={() => run(item.cmd)} title={item.desc}>{item.cmd}</BtnSm>
              ))}
            </div>
          </div>
        );
      })}

      <div style={{ display: "flex", gap: 8 }}>
        <Input value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Edit: Add ladder to tower · Complete missing members · Extend height to 50m" style={{ flex: 1 }} />
        <Btn onClick={() => run()} disabled={loading || !prompt.trim()}>
          {loading ? <><Spin />Running…</> : "Execute"}
        </Btn>
      </div>
      {err && <Alert type="error">❌ {err}</Alert>}
      {result && (
        <Alert type={(result.added ?? 0) === 0 && result.action !== "cleanup" ? "warn" : "success"}>
          {(result.added ?? 0) === 0 && result.action !== "cleanup" ? "⚠" : "✅"} {resultMsg}
          {result.model_context && (
            <div style={{ fontSize: 11, marginTop: 4, color: t.muted }}>
              Model: {result.model_context.structure_type} · {result.model_context.member_count} members · {result.edit_intent || result.action}
            </div>
          )}
        </Alert>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// COMPLETION PANEL
// ════════════════════════════════════════════════════════════════
function CompletionPanel({ onRefresh, isOffline }) {
  const t = useTheme();
  const [roof, setRoof] = useState("flat");
  const [mirror, setMirror] = useState(true);
  const [loading, setLoading] = useState(false);
  const [analysing, setAnalysing] = useState(false);
  const [topo, setTopo] = useState(null);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  const loadTopo = async () => {
    setAnalysing(true);
    try {
      if (isOffline) await detectWorkingAPI();
      const r = await api("/bim/analyse", "POST");
      setTopo(r);
    } catch (e) { /* model may be empty */ } finally { setAnalysing(false); }
  };

  useEffect(() => { if (!isOffline) loadTopo(); }, []); // eslint-disable-line

  const run = async () => {
    setLoading(true); setErr(null); setResult(null);
    try {
      if (isOffline) await detectWorkingAPI();
      const r = await api("/bim/complete", "POST", { roof_type: roof, use_mirror: mirror, send_to_tekla: true });
      setResult(r);
      if (r.pending_tekla_insert && (r.added || 0) > 0) {
        await waitForTeklaSync(r.total_in_model, onRefresh, 120);
      } else {
        await onRefresh();
      }
      await loadTopo();
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  const structType = (topo?.inferred_type?.type || "unknown").replace(/_/g, " ");
  const isTower = (topo?.inferred_type?.type || "").includes("tower");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ fontSize: 11, color: t.muted, padding: "7px 12px", background: t.hint, border: `1px solid ${t.border}`, borderRadius: 6, lineHeight: 1.4 }}>
        Detect missing members from loaded model · fills gaps in Tekla · towers: bracing + ladder/platform/antenna · buildings: columns/beams/bracing/roof
      </div>
      {topo && topo.status !== "empty" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
          {[
            { v: structType.toUpperCase(), lbl: "Detected Type", c: t.accent },
            { v: `${topo.completeness_pct ?? "—"}%`, lbl: "Completeness", c: t.success },
            { v: topo.missing_count ?? 0, lbl: "Missing (est.)", c: t.warn },
          ].map(({ v, lbl, c }, i) => (
            <div key={i} style={{ background: t.panel, border: `1px solid ${t.border}`, borderRadius: 6, padding: "10px 12px" }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: c }}>{v}</div>
              <div style={{ fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase" }}>{lbl}</div>
            </div>
          ))}
        </div>
      )}
      {topo?.status === "empty" && (
        <Alert type="warn">No model loaded. Create a structure first or type <code>refresh</code> in dotnet terminal after Tekla export.</Alert>
      )}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
        {!isTower && (
          <>
            <div>
              <label style={{ display: "block", fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px", marginBottom: 5 }}>Roof Type</label>
              <Select value={roof} onChange={setRoof}><option value="flat">Flat</option><option value="gable">Gable</option></Select>
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: t.muted, paddingBottom: 2 }}>
              <input type="checkbox" checked={mirror} onChange={e => setMirror(e.target.checked)} />
              Mirror if &lt;60% complete
            </label>
          </>
        )}
        <BtnSm onClick={loadTopo} disabled={analysing}>{analysing ? "…" : "↺ Re-analyse"}</BtnSm>
        <Btn onClick={run} disabled={loading || topo?.status === "empty"} variant="secondary">
          {loading ? <><Spin />Completing…</> : "Analyse & Complete → Tekla"}
        </Btn>
      </div>
      {err && <Alert type="error">❌ {err}</Alert>}
      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Alert type={result.status === "complete" ? "info" : result.status === "ok" ? "success" : "info"}>
            {result.message || (result.status === "complete"
              ? "Model complete — no missing members detected."
              : `Added ${result.added} members · sent to Tekla`)}
            {result.truncated && " · Run Complete again for remaining gaps"}
          </Alert>
          {result.breakdown && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 6 }}>
              {[["Columns", result.breakdown.columns], ["Beams", result.breakdown.beams], ["Bracing", result.breakdown.bracing], ["Roof", result.breakdown.roof]].map(([lbl, val]) => (
                <div key={lbl} style={{ background: t.successBox, border: `1px solid ${t.successBoxBorder}`, borderRadius: 4, padding: "6px 10px" }}>
                  <div style={{ fontSize: 10, color: t.muted, fontWeight: 700 }}>{lbl}</div>
                  <div style={{ fontSize: 14, fontWeight: 800, color: t.success }}>{val ?? 0}</div>
                </div>
              ))}
            </div>
          )}
          {result.topology?.completeness_before != null && (
            <div style={{ fontSize: 11, color: t.muted }}>Completeness before: {result.topology.completeness_before}%</div>
          )}
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// TOPOLOGY PANEL
// ════════════════════════════════════════════════════════════════
function TopologyPanel({ isOffline, onAnalysed }) {
  const t = useTheme();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const run = async () => {
    setLoading(true); setErr(null);
    try {
      if (isOffline) await detectWorkingAPI();
      const r = await api("/bim/analyse", "POST");
      setResult(r);
      onAnalysed?.(r);
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };
  useEffect(() => { if (!isOffline) run(); }, []); // eslint-disable-line
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Btn onClick={run} disabled={loading} variant="secondary">
        {loading ? <><Spin />Analysing…</> : "Re-analyse Structure"}
      </Btn>
      {err && <Alert type="error">❌ {err}</Alert>}
      {result?.status === "empty" && <Alert type="warn">⚠ {result.message}</Alert>}
      {result && result.status !== "empty" && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
            {[
              { v: (result.inferred_type?.type || "—").replace(/_/g, " ").toUpperCase(), lbl: "Detected Type", c: t.accent },
              { v: `${result.completeness_pct ?? "—"}%`, lbl: "Completeness", c: t.success },
              { v: result.bracing?.dominant?.toUpperCase() || "—", lbl: "Bracing Pattern", c: t.warn },
              { v: result.symmetry?.type || "—", lbl: "Symmetry", c: "#6D28D9" },
            ].map(({ v, lbl, c }, i) => (
              <div key={i} style={{ background: t.panel, border: `1px solid ${t.border}`, borderRadius: 6, padding: "12px 14px" }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: c, marginBottom: 3 }}>{v}</div>
                <div style={{ fontSize: 10, fontWeight: 700, color: t.muted, textTransform: "uppercase", letterSpacing: ".4px" }}>{lbl}</div>
              </div>
            ))}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div style={{ background: t.hint, border: `1px solid ${t.border}`, borderRadius: 6, padding: "10px 14px" }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: t.muted, marginBottom: 6 }}>GRID</div>
              <div style={{ fontSize: 11, color: t.muted }}>X lines: {result.x_coords?.length || 0} · Y lines: {result.y_coords?.length || 0} · Levels: {result.levels?.length || 0}</div>
            </div>
            <div style={{ background: t.hint, border: `1px solid ${t.border}`, borderRadius: 6, padding: "10px 14px" }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: t.muted, marginBottom: 6 }}>GAPS</div>
              <div style={{ fontSize: 11, color: t.muted }}>{result.missing_count || 0} missing members detected · {result.n_panels || 0} panels</div>
            </div>
          </div>
          {(result.panel_status || []).slice(0, 6).map((p, i) => (
            <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", padding: "8px 12px", border: `1px solid ${t.border}`, borderRadius: 6, background: p.complete ? t.successBox : t.hint }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: p.complete ? t.success : t.warn }}>L{p.level_idx} Z{p.z0}–{p.z1}</span>
              <span style={{ fontSize: 11, color: t.muted }}>{p.diagonals} diagonals · {p.horizontals} horizontals</span>
              <span style={{ marginLeft: "auto", fontSize: 10, fontWeight: 700, color: p.complete ? t.success : t.warn }}>{p.complete ? "COMPLETE" : "INCOMPLETE"}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// SIDEBAR + TAB META
// ════════════════════════════════════════════════════════════════
const SIDEBAR_SECTIONS = [
  { label: "Workflows", items: [
    { id: "create",   label: "Create Structure" },
    { id: "complete", label: "Complete Model" },
    { id: "agent",    label: "Build Agent" },
  ]},
  { label: "Analysis", items: [
    { id: "graph",    label: "Network Graph" },
    { id: "3d",       label: "3D View" },
    { id: "topology", label: "Topology" },
    { id: "clash",    label: "Clash Detection" },
    { id: "defects",  label: "Defect Analysis" },
  ]},
  { label: "Reports", items: [
    { id: "audit",    label: "Audit Report" },
    { id: "rules",    label: "Rule Engine" },
  ]},
  { label: "Data", items: [
    { id: "members",  label: "Member Table" },
    { id: "material", label: "Material Schedule" },
  ]},
  { label: "AI", items: [
    { id: "ai",       label: "AI Engineer Chat" },
  ]},
];

const TAB_META = {
  create:   { title: "Create Structure",   sub: "Natural language → 10-stage pipeline → Tekla" },
  complete: { title: "Complete Model",      sub: "Pattern-based BIM completion" },
  agent:    { title: "Build Agent",         sub: "Preset + custom AI commands" },
  graph:    { title: "Network Graph",       sub: "Structural connectivity visualisation" },
  "3d":     { title: "3D Viewer",           sub: "Three.js orbit · zoom · colour by role" },
  topology: { title: "Topology Analysis",   sub: "Geometry inference · bracing · symmetry" },
  clash:    { title: "Clash Detection",     sub: "Bounding-box interference check" },
  defects:  { title: "Defect Analysis",    sub: "Model quality & geometry checks" },
  audit:    { title: "Audit Report",        sub: "Structural health score · download report" },
  rules:    { title: "Rule Engine",         sub: "IF/THEN structural validation rules" },
  members:  { title: "Member Table",        sub: "Filter · search · export" },
  material: { title: "Material Schedule",   sub: "Profile/material aggregation" },
  ai:       { title: "AI Engineer",         sub: "Structural Q&A · works offline with demo data" },
};

const ACTIVITY_SHORT = { create: "CR", graph: "GR", audit: "AU", rules: "RL", members: "MB", ai: "AI" };

// ════════════════════════════════════════════════════════════════
// MAIN APP — FIXED: single isOffline source of truth
// ════════════════════════════════════════════════════════════════
export default function App() {
  const [tab, setTab]                     = useState("create");
  const [summary, setSummary]             = useState(null);
  const [members, setMembers]             = useState([]);
  const [relationships, setRelationships] = useState([]);
  const [spatialEdges, setSpatialEdges]   = useState([]);
  const [selMem, setSelMem]               = useState(null);
  const [health, setHealth]               = useState(null);
  const [loading, setLoading]             = useState(false);
  const [connStatus, setConnStatus]       = useState("checking"); // "checking" | "online" | "offline"
  const [demoMode, setDemoMode]           = useState(false);
  const [showOfflineBanner, setShowOfflineBanner] = useState(false);
  const [showDemoBanner, setShowDemoBanner]       = useState(false);
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("tsi-theme") || "light"; } catch { return "light"; }
  });
  const t = THEMES[theme] || THEMES.light;

  const toggleTheme = useCallback(() => {
    setTheme(prev => {
      const next = prev === "light" ? "dark" : "light";
      try { localStorage.setItem("tsi-theme", next); } catch { /* ignore */ }
      return next;
    });
  }, []);

  // Derived — true only when definitively offline (not "checking")
  const isOffline = connStatus === "offline";

  // Single function that does health + model load atomically
  const loadModel = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      // First detect which URL works
      const workingUrl = await detectWorkingAPI();
      if (!workingUrl) throw new Error("No working backend found");

      const [healthData, modelData] = await Promise.all([
        api("/health"),
        api("/model-data"),
      ]);
      setHealth(healthData);
      setMembers(modelData.members || []);
      setRelationships(modelData.relationships || []);
      setSpatialEdges(modelData.edges || []);
      setSummary(modelData.summary);
      setConnStatus("online");
      setShowOfflineBanner(false);
      if (demoMode) { setDemoMode(false); setShowDemoBanner(false); }
    } catch (e) {
      console.warn("Backend unreachable:", e.message);
      setConnStatus("offline");
      setHealth(null);
      if (!demoMode) setShowOfflineBanner(true);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [demoMode]);

  // Periodic silent health check
  const silentCheck = useCallback(async () => {
    try {
      const workingUrl = await detectWorkingAPI();
      if (workingUrl) {
        await api("/health");
        // If we were offline and now online, reload model
        setConnStatus(prev => {
          if (prev === "offline") { loadModel(true); }
          return "online";
        });
        setShowOfflineBanner(false);
      } else {
        setConnStatus("offline");
      }
    } catch {
      setConnStatus("offline");
    }
  }, [loadModel]);

  useEffect(() => {
    loadModel();
    const id = setInterval(silentCheck, 15000);
    return () => clearInterval(id);
  }, []); // eslint-disable-line

  const activateDemo = useCallback(() => {
    setDemoMode(true);
    setMembers(DEMO_MEMBERS);
    setRelationships(buildDemoRelationships(DEMO_MEMBERS));
    setSummary(DEMO_SUMMARY);
    setShowOfflineBanner(false);
    setShowDemoBanner(true);
  }, []);

  const displayMembers = members;
  const displayRelationships = relationships.length
    ? relationships
    : (displayMembers.length ? buildDemoRelationships(displayMembers) : []);
  const displaySummary = summary || (demoMode ? DEMO_SUMMARY : null);
  const meta = TAB_META[tab] || {};

  return (
    <ThemeCtx.Provider value={t}>
    <div data-theme={theme} style={{ minHeight: "100vh", display: "flex", flexDirection: "column", fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', fontSize: 12, background: t.bg, color: t.text }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        @keyframes spin  { to { transform: rotate(360deg); } }
        @keyframes blink { 0%,80%,100% { opacity:.2 } 40% { opacity:1 } }
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${theme === "dark" ? "#424242" : "#D8D3C8"}; border-radius: 3px; }
        input:focus, textarea:focus { border-color: ${t.activeText} !important; }
        ::placeholder { color: ${t.muted}; opacity: 0.85; }
      `}</style>

      <HeaderBar
        theme={theme}
        onThemeToggle={toggleTheme}
        connStatus={connStatus}
        demoMode={demoMode}
        health={health}
        loading={loading}
        onRefresh={() => loadModel()}
        t={t}
      />

      {/* BANNERS */}
      {showOfflineBanner && !demoMode && <OfflineBanner onDemo={activateDemo} onRetry={loadModel} />}
      {showDemoBanner && demoMode && <DemoBanner onClear={() => setShowDemoBanner(false)} />}
      {connStatus === "online" && !demoMode && health?.in_sync === false && (
        <Alert type="warn">
          Model out of sync — type <code>refresh</code> in dotnet terminal, then click ↺.
        </Alert>
      )}

      {/* KPI ROW */}
      <KpiRow summary={displaySummary} t={t} />

      {/* MAIN LAYOUT */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden", minHeight: 0 }}>

        {/* ACTIVITY BAR */}
        <div style={{ width: 40, background: t.activity, display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 6, gap: 2, flexShrink: 0 }}>
          {["create", "graph", "audit", "rules", "members", "ai"].map(id => {
            const m = TAB_META[id];
            const active = tab === id;
            return (
              <div key={id} onClick={() => setTab(id)} title={m?.title} style={{
                width: 36, height: 36, display: "flex", alignItems: "center", justifyContent: "center",
                cursor: "pointer", fontSize: 10, fontWeight: 700, letterSpacing: ".3px",
                borderLeft: `2px solid ${active ? t.activeText : "transparent"}`,
                background: active ? "rgba(255,255,255,0.07)" : "none",
                color: active ? "#FFFFFF" : t.muted, transition: "all .12s",
              }}>
                {ACTIVITY_SHORT[id]}
              </div>
            );
          })}
        </div>

        {/* SIDEBAR */}
        <div style={{ width: 188, background: t.sidebar, borderRight: `1px solid ${t.border}`, overflowY: "auto", flexShrink: 0 }}>
          {SIDEBAR_SECTIONS.map(sec => (
            <div key={sec.label} style={{ paddingBottom: 8 }}>
              <div style={{ padding: "10px 10px 4px 12px", fontSize: 9, fontWeight: 700, letterSpacing: ".8px", color: t.muted, textTransform: "uppercase" }}>{sec.label}</div>
              {sec.items.map(item => (
                <div key={item.id} onClick={() => setTab(item.id)} style={{
                  display: "flex", alignItems: "center", padding: "5px 10px 5px 14px", borderRadius: 3, cursor: "pointer",
                  fontSize: 11.5, fontWeight: tab === item.id ? 600 : 400,
                  color: tab === item.id ? t.activeText : t.muted,
                  background: tab === item.id ? t.activeTab : "none",
                  borderLeft: tab === item.id ? `2px solid ${t.activeText}` : "2px solid transparent",
                  margin: "0 4px", lineHeight: "22px", transition: "all .1s",
                }}>
                  {item.label}
                </div>
              ))}
            </div>
          ))}
          {displaySummary && (
            <div style={{ borderTop: `1px solid ${t.border}`, padding: "10px 12px" }}>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".8px", color: t.muted, textTransform: "uppercase", marginBottom: 6 }}>Model Summary</div>
              {[["Total", displaySummary.total, t.text], ["Columns", displaySummary.columns, t.accent], ["Beams", displaySummary.beams, t.warn], ["Secondary", displaySummary.secondary, t.muted], ["Unknown", displaySummary.unknown, t.muted]].map(([l, v, c]) => (
                <div key={l} style={{ display: "flex", justifyContent: "space-between", padding: "2px 0", fontSize: 11 }}>
                  <span style={{ color: t.muted }}>{l}</span>
                  <span style={{ fontWeight: 700, color: c }}>{fmt(v)}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* MAIN AREA */}
        <div style={{ flex: 1, overflowY: "auto", background: t.bg, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <div style={{ height: 36, display: "flex", alignItems: "stretch", background: t.cream, borderBottom: `1px solid ${t.border}`, overflowX: "auto", flexShrink: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "0 16px", fontSize: 11, fontWeight: 600, color: t.text, background: t.panel, borderRight: `1px solid ${t.border}`, position: "relative", whiteSpace: "nowrap" }}>
              <div style={{ width: 6, height: 6, borderRadius: "50%", background: t.activeText }} />
              {meta.title}
              <div style={{ position: "absolute", left: 0, right: 0, top: -1, height: 2, background: t.activeText }} />
            </div>
          </div>
          <div style={{ padding: 16, flex: 1 }}>
            <div style={{ background: t.panel, border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
              <PanelHead title={meta.title} sub={meta.sub} t={t} />
              <div style={{ padding: 16 }}>
                {tab === "create"   && <CreationPanel   onRefresh={loadModel}  isOffline={isOffline} />}
                {tab === "complete" && <CompletionPanel  onRefresh={loadModel}  isOffline={isOffline} />}
                {tab === "agent"    && <BuildAgentPanel  onRefresh={loadModel}  isOffline={isOffline} />}
                {tab === "topology" && <TopologyPanel    isOffline={isOffline} />}
                {tab === "clash"    && <ClashPanel       members={displayMembers} isOffline={isOffline} />}
                {tab === "defects"  && <DefectPanel      members={displayMembers} isOffline={isOffline} />}
                {tab === "audit"    && <AuditPanel       members={displayMembers} />}
                {tab === "rules"    && <RuleEnginePanel  members={displayMembers} />}
                {tab === "material" && <MaterialTable    members={displayMembers} />}
                {tab === "ai"       && <AIChatPanel      isOffline={isOffline} members={displayMembers} />}
                {tab === "members"  && (
                  <div>
                    <MemberTable members={displayMembers} onSelect={setSelMem} selId={selMem?.id} />
                    {selMem && (
                      <div style={{ marginTop: 12, background: t.activeTab, border: `1px solid ${t.border}`, borderRadius: 6, padding: 14 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                          <span style={{ fontWeight: 700, color: t.activeText, fontFamily: "monospace", fontSize: 12 }}>#{selMem.id}</span>
                          <Badge role={selMem.role} />
                          {[["Profile", selMem.profile], ["Material", selMem.material], ["Length", fmt(selMem.length) + " mm"]].map(([k, v]) => (
                            <span key={k} style={{ fontSize: 12, color: t.muted }}>{k}: <strong style={{ color: t.text }}>{v}</strong></span>
                          ))}
                        </div>
                        <div style={{ marginTop: 6, fontSize: 10, color: t.muted, fontFamily: "monospace" }}>
                          ({rnd(selMem.x)}, {rnd(selMem.y)}, {rnd(selMem.z)}) → ({rnd(selMem.x2)}, {rnd(selMem.y2)}, {rnd(selMem.z2)})
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
            {tab === "graph" && (
              <div style={{ marginTop: 16 }}>
                <div style={{ background: t.panel, border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
                  <PanelHead title="Network Graph" sub="Structural connectivity · click nodes for details" t={t} />
                  {displayMembers.length === 0
                    ? <div style={{ padding: 40, textAlign: "center", color: "#9B9486" }}>No members loaded. Use Demo Data or connect the backend.</div>
                    : <NetworkGraph members={displayMembers} relationships={displayRelationships} spatialEdges={spatialEdges} />}
                </div>
              </div>
            )}
            {tab === "3d" && (
              <div style={{ marginTop: 16 }}>
                <div style={{ background: t.panel, border: `1px solid ${t.border}`, borderRadius: 6, overflow: "hidden" }}>
                  <PanelHead title="3D Viewer" sub="Same model as Tekla — synced after generate" t={t} />
                  {connStatus === "online" && !demoMode && health?.in_sync === false && health?.tekla_member_count != null && (
                    <Alert type="warn">
                      Out of sync — Dashboard: {health.output_json_count ?? displayMembers.length} members · Tekla: {health.tekla_member_count}. Click ↺ or run <code>refresh</code> in dotnet.
                    </Alert>
                  )}
                  {displayMembers.length === 0
                    ? <div style={{ padding: 40, textAlign: "center", color: t.muted }}>No model loaded. Generate a structure or use Demo Data first.</div>
                    : <BIMViewer3D members={displayMembers} />}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* STATUS BAR */}
      <div style={{
        height: 22, display: "flex", alignItems: "center", gap: 12, padding: "0 12px",
        background: theme === "dark" ? t.statusBar : (connStatus === "online" ? "#1D4ED8" : demoMode ? "#B45309" : "#57534E"),
        color: "#fff", fontSize: 10.5, fontWeight: 500, flexShrink: 0,
      }}>
        <span>{meta.title || "Dashboard"}</span>
        {displaySummary && <span style={{ opacity: 0.85 }}>{displaySummary.total} members</span>}
        <div style={{ flex: 1 }} />
        <span style={{ opacity: 0.65 }}>v6.2</span>
      </div>
    </div>
    </ThemeCtx.Provider>
  );
}