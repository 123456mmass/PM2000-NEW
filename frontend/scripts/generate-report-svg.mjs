#!/usr/bin/env node

import fs from 'node:fs/promises';
import path from 'node:path';

const API_BASE = process.env.PM2230_API_BASE || 'http://localhost:8002';
const OUTPUT_FILE = process.env.PM2230_REPORT_OUTPUT || path.join(process.cwd(), 'public', 'report-template-live.svg');
const WIDTH = 1240;
const HEIGHT = 1754;

const sample = {
  page1: {
    timestamp: new Date().toISOString(),
    status: 'SAMPLE',
    V_LN1: 231.4,
    V_LN2: 229.8,
    V_LN3: 232.6,
    V_LN_avg: 231.3,
    V_LL12: 400.2,
    V_LL23: 397.5,
    V_LL31: 402.1,
    V_LL_avg: 399.9,
    I_L1: 5.2,
    I_L2: 4.8,
    I_L3: 5.6,
    I_N: 0.9,
    I_avg: 5.2,
    Freq: 49.98,
  },
  page2: {
    timestamp: new Date().toISOString(),
    status: 'SAMPLE',
    P_L1: 11.4,
    P_L2: 10.9,
    P_L3: 12.0,
    P_Total: 34.3,
    S_L1: 12.2,
    S_L2: 11.5,
    S_L3: 12.9,
    S_Total: 36.9,
    Q_L1: 2.7,
    Q_L2: 2.3,
    Q_L3: 2.6,
    Q_Total: 7.6,
  },
  page3: {
    timestamp: new Date().toISOString(),
    status: 'SAMPLE',
    THDv_L1: 3.2,
    THDv_L2: 3.4,
    THDv_L3: 3.6,
    THDi_L1: 15.1,
    THDi_L2: 16.2,
    THDi_L3: 17.4,
    V_unb: 1.2,
    U_unb: 1.1,
    I_unb: 7.6,
    PF_L1: 0.95,
    PF_L2: 0.94,
    PF_L3: 0.96,
    PF_Total: 0.952,
  },
  page4: {
    timestamp: new Date().toISOString(),
    status: 'SAMPLE',
    kWh_Total: 23518.4,
    kVAh_Total: 25273.8,
    kvarh_Total: 6583.1,
  },
};

function esc(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function fmt(value, digits = 2) {
  if (!Number.isFinite(value)) return '-';
  return value.toFixed(digits);
}

function avg(values) {
  return values.reduce((sum, v) => sum + v, 0) / (values.length || 1);
}

function std(values) {
  if (values.length < 2) return 0;
  const m = avg(values);
  return Math.sqrt(avg(values.map((v) => (v - m) ** 2)));
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

async function fetchJson(url) {
  try {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function fetchCsv(url) {
  try {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

function parseCsvRows(csvText) {
  if (!csvText) return [];
  const lines = csvText.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) return [];

  const headers = lines[0].split(',');
  return lines.slice(1).map((line) => {
    const cols = line.split(',');
    const row = {};
    headers.forEach((header, idx) => {
      row[header] = cols[idx] ?? '';
    });
    return row;
  });
}

function numberOr(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function isBadStatus(status) {
  const s = String(status || '').toUpperCase();
  return !s || s === 'NOT_CONNECTED' || s === 'ERROR' || s.startsWith('ERROR');
}

function hasAnySignal(obj, keys, threshold = 0.01) {
  return keys.some((key) => Math.abs(numberOr(obj?.[key], 0)) > threshold);
}

function findLatestUsableRow(rows) {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const row = rows[i];
    const status = String(row.status || '').toUpperCase();
    const hasLiveSignal = Math.abs(numberOr(row.V_LN1, 0)) > 0.5 || Math.abs(numberOr(row.P_Total, 0)) > 0.05;
    if (!isBadStatus(status) || hasLiveSignal) return row;
  }
  return null;
}

function buildPagesFromRow(row) {
  if (!row) return null;
  const status = isBadStatus(row.status) ? 'LOG_FALLBACK' : String(row.status);
  const timestamp = row.timestamp || new Date().toISOString();

  return {
    page1: {
      timestamp,
      status,
      V_LN1: numberOr(row.V_LN1),
      V_LN2: numberOr(row.V_LN2),
      V_LN3: numberOr(row.V_LN3),
      V_LN_avg: numberOr(row.V_LN_avg),
      V_LL12: numberOr(row.V_LL12),
      V_LL23: numberOr(row.V_LL23),
      V_LL31: numberOr(row.V_LL31),
      V_LL_avg: numberOr(row.V_LL_avg),
      I_L1: numberOr(row.I_L1),
      I_L2: numberOr(row.I_L2),
      I_L3: numberOr(row.I_L3),
      I_N: numberOr(row.I_N),
      I_avg: numberOr(row.I_avg),
      Freq: numberOr(row.Freq),
    },
    page2: {
      timestamp,
      status,
      P_L1: numberOr(row.P_L1),
      P_L2: numberOr(row.P_L2),
      P_L3: numberOr(row.P_L3),
      P_Total: numberOr(row.P_Total),
      S_L1: numberOr(row.S_L1),
      S_L2: numberOr(row.S_L2),
      S_L3: numberOr(row.S_L3),
      S_Total: numberOr(row.S_Total),
      Q_L1: numberOr(row.Q_L1),
      Q_L2: numberOr(row.Q_L2),
      Q_L3: numberOr(row.Q_L3),
      Q_Total: numberOr(row.Q_Total),
    },
    page3: {
      timestamp,
      status,
      THDv_L1: numberOr(row.THDv_L1),
      THDv_L2: numberOr(row.THDv_L2),
      THDv_L3: numberOr(row.THDv_L3),
      THDi_L1: numberOr(row.THDi_L1),
      THDi_L2: numberOr(row.THDi_L2),
      THDi_L3: numberOr(row.THDi_L3),
      V_unb: numberOr(row.V_unb),
      U_unb: numberOr(row.U_unb),
      I_unb: numberOr(row.I_unb),
      PF_L1: numberOr(row.PF_L1),
      PF_L2: numberOr(row.PF_L2),
      PF_L3: numberOr(row.PF_L3),
      PF_Total: numberOr(row.PF_Total),
    },
    page4: {
      timestamp,
      status,
      kWh_Total: numberOr(row.kWh_Total),
      kVAh_Total: numberOr(row.kVAh_Total),
      kvarh_Total: numberOr(row.kvarh_Total),
    },
  };
}

function resolvePage(live, logFallback, sampleFallback, keys) {
  const page = live || {};
  const liveUsable = !isBadStatus(page.status) && hasAnySignal(page, keys);
  if (liveUsable) return page;

  if (logFallback && hasAnySignal(logFallback, keys)) return logFallback;

  return {
    ...sampleFallback,
    timestamp: page.timestamp || logFallback?.timestamp || sampleFallback.timestamp || new Date().toISOString(),
    status: isBadStatus(page.status) ? 'SAMPLE' : String(page.status || sampleFallback.status || 'SAMPLE'),
  };
}

function trendFromRows(rows) {
  const values = rows
    .map((row) => Number(row.P_Total || row.P_Total?.trim?.() || 0))
    .filter((value) => Number.isFinite(value) && value >= 0);

  const recent = values.slice(-15);
  if (recent.length >= 6 && recent.some((v) => v > 0)) {
    return recent;
  }

  const positive = recent.filter((v) => v > 0);
  const base = positive.length ? avg(positive) : sample.page2.P_Total;
  const synthetic = [
    base * 0.88, base * 0.9, base * 0.87, base * 0.92, base * 0.95,
    base * 0.93, base * 0.97, base * 1.01, base * 1.03, base * 1.0,
    base * 1.05, base * 1.08, base * 1.06, base * 1.1, base * 1.09,
  ];
  return synthetic.map((v) => Number(v.toFixed(3)));
}

function buildPath(values, x, y, width, height) {
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const range = maxVal - minVal || 1;
  const dx = width / Math.max(values.length - 1, 1);

  const pts = values.map((val, idx) => {
    const px = x + idx * dx;
    const py = y + height - ((val - minVal) / range) * height;
    return [px, py];
  });

  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const area = `${line} L${(x + width).toFixed(1)} ${(y + height).toFixed(1)} L${x.toFixed(1)} ${(y + height).toFixed(1)} Z`;

  return {
    line,
    area,
    minVal,
    maxVal,
    avgVal: avg(values),
    stdVal: std(values),
    last: values[values.length - 1],
  };
}

function levelFromScore(score) {
  if (score >= 85) return 'good';
  if (score >= 65) return 'watch';
  return 'critical';
}

function scoreForRange(value, goodMin, goodMax, warnMin, warnMax) {
  if (value >= goodMin && value <= goodMax) return 100;
  if (value >= warnMin && value <= warnMax) return 70;
  return 35;
}

function scoreForUpper(value, goodMax, warnMax) {
  if (value <= goodMax) return 100;
  if (value <= warnMax) return 70;
  return 35;
}

function scoreForLower(value, goodMin, warnMin) {
  if (value >= goodMin) return 100;
  if (value >= warnMin) return 70;
  return 35;
}

function buildAnalysis(data, trendStats, dataRowsCount) {
  const { page1, page2, page3, page4 } = data;

  const vAvg = page1.V_LN_avg || avg([page1.V_LN1, page1.V_LN2, page1.V_LN3]);
  const thdv = avg([page3.THDv_L1, page3.THDv_L2, page3.THDv_L3]);
  const thdi = avg([page3.THDi_L1, page3.THDi_L2, page3.THDi_L3]);
  const pf = Math.abs(page3.PF_Total || 0);
  const currentMean = avg([page1.I_L1, page1.I_L2, page1.I_L3]);
  const currentUnbalance = currentMean > 0
    ? ((Math.max(page1.I_L1, page1.I_L2, page1.I_L3) - Math.min(page1.I_L1, page1.I_L2, page1.I_L3)) / currentMean) * 100
    : 0;

  const voltageScore = avg([
    scoreForRange(vAvg, 218, 242, 207, 253),
    scoreForRange(page1.Freq, 49.8, 50.2, 49.5, 50.5),
    scoreForUpper(page3.V_unb, 2, 5),
  ]);

  const loadScore = avg([
    scoreForLower(pf, 0.95, 0.9),
    scoreForUpper(currentUnbalance, 10, 20),
    scoreForUpper(Math.abs(page2.P_Total - trendStats.avgVal), 3.0, 6.0),
  ]);

  const qualityScore = avg([
    scoreForUpper(thdv, 5, 8),
    scoreForUpper(thdi, 20, 30),
    scoreForUpper(page3.I_unb, 10, 20),
  ]);

  const energyPf = page4.kWh_Total / (page4.kVAh_Total || 1);
  const reactiveRatio = page4.kvarh_Total / (page4.kWh_Total || 1);

  const energyScore = avg([
    scoreForLower(energyPf, 0.9, 0.8),
    scoreForUpper(reactiveRatio, 0.3, 0.6),
    scoreForUpper((trendStats.stdVal / (trendStats.avgVal || 1)) * 100, 15, 30),
  ]);

  const overallScore = Math.round(voltageScore * 0.3 + loadScore * 0.25 + qualityScore * 0.25 + energyScore * 0.2);

  const alerts = [];
  if (pf < 0.9) alerts.push('PF ต่ำกว่ามาตรฐานช่วงโหลดสูง');
  if (thdi > 20) alerts.push('THDi สูงเกินเกณฑ์เฝ้าระวัง');
  if (page3.I_unb > 10) alerts.push('Current Unbalance สูง ควร balance phase');
  if (thdv > 5) alerts.push('THDv สูงกว่าค่าแนะนำ');
  if (!alerts.length) alerts.push('ไม่พบค่าที่เกินเกณฑ์วิกฤตในช่วงข้อมูลที่ใช้วิเคราะห์');

  const recommendations = [];
  if (thdi > 15) recommendations.push('ตรวจสอบโหลดไม่เชิงเส้น (VFD/rectifier) และพิจารณา harmonic filter');
  if (currentUnbalance > 8) recommendations.push('กระจายโหลดเฟสใหม่เพื่อลด I_unbalance และ neutral current');
  if (pf < 0.95) recommendations.push('ปรับ step capacitor bank ให้ตอบสนอง reactive demand จริง');
  recommendations.push('ตั้ง alert อัตโนมัติ: PF<0.90, THDi>20%, I_unb>10%');
  recommendations.push('เก็บ trend 24 ชั่วโมงต่อเนื่องเพื่อยืนยันรูปแบบโหลดและช่วง peak');

  const dataCompleteness = clamp((dataRowsCount / 3600) * 100, 35, 99);
  const modelCertainty = clamp((overallScore * 0.82) + (alerts.length ? 4 : 9), 55, 97);
  const actionability = clamp(88 + (alerts.length >= 2 ? 5 : 0), 70, 98);

  return {
    vAvg,
    thdv,
    thdi,
    pf,
    currentUnbalance,
    energyPf,
    reactiveRatio,
    scores: {
      voltage: Math.round(voltageScore),
      load: Math.round(loadScore),
      quality: Math.round(qualityScore),
      energy: Math.round(energyScore),
      overall: overallScore,
    },
    alerts,
    recommendations,
    level: levelFromScore(overallScore),
    confidence: {
      completeness: Math.round(dataCompleteness),
      certainty: Math.round(modelCertainty),
      actionability: Math.round(actionability),
    },
  };
}

function scoreColor(score) {
  if (score >= 85) return '#16a34a';
  if (score >= 65) return '#f59e0b';
  return '#ef4444';
}

function buildSvg(data, trend, trendStats, analysis, dataRowsCount) {
  const ts = new Date(data.page1.timestamp || new Date().toISOString());
  const thTime = ts.toLocaleString('th-TH');

  const scoreBars = [
    ['Voltage Stability', analysis.scores.voltage],
    ['Load Balance', analysis.scores.load],
    ['Power Quality', analysis.scores.quality],
    ['Energy Efficiency', analysis.scores.energy],
  ];

  const riskList = analysis.alerts.slice(0, 4);
  while (riskList.length < 4) riskList.push('สถานะเสถียร ไม่มีความเสี่ยงเพิ่ม');

  const trendPath = buildPath(trend, 92, 572, 672, 174);

  const riskFill = (idx) => {
    if (idx === 0 && analysis.level === 'critical') return 'url(#riskHigh)';
    if (idx === 0 && analysis.level === 'watch') return 'url(#riskMid)';
    if (idx <= 1) return 'url(#riskMid)';
    return 'url(#riskLow)';
  };

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="PM2230 Dynamic One-Page Report">
  <defs>
    <linearGradient id="pageBg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1220"/><stop offset="60%" stop-color="#111b30"/><stop offset="100%" stop-color="#0c1728"/>
    </linearGradient>
    <linearGradient id="sheet" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f8fbff"/><stop offset="100%" stop-color="#eef4fa"/>
    </linearGradient>
    <linearGradient id="hero" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f3c7d"/><stop offset="100%" stop-color="#0ea5e9"/>
    </linearGradient>
    <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.45"/><stop offset="100%" stop-color="#38bdf8" stop-opacity="0.03"/>
    </linearGradient>
    <linearGradient id="riskLow" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="#16a34a"/><stop offset="100%" stop-color="#22c55e"/></linearGradient>
    <linearGradient id="riskMid" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="#d97706"/><stop offset="100%" stop-color="#f59e0b"/></linearGradient>
    <linearGradient id="riskHigh" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="#dc2626"/><stop offset="100%" stop-color="#ef4444"/></linearGradient>
    <style>
      .h1 { font: 700 44px 'Segoe UI', Arial, sans-serif; fill: #fff; }
      .h2 { font: 700 25px 'Segoe UI', Arial, sans-serif; fill: #0f172a; }
      .h3 { font: 700 20px 'Segoe UI', Arial, sans-serif; fill: #1e293b; }
      .label { font: 600 16px 'Segoe UI', Arial, sans-serif; fill: #64748b; }
      .value { font: 700 33px 'Segoe UI', Arial, sans-serif; fill: #0f172a; }
      .small { font: 500 14px 'Segoe UI', Arial, sans-serif; fill: #64748b; }
      .body { font: 500 15px 'Segoe UI', Arial, sans-serif; fill: #334155; }
      .bullet { font: 500 15px 'Segoe UI', Arial, sans-serif; fill: #1f2937; }
      .mono { font: 600 14px Consolas, 'Courier New', monospace; fill: #1e293b; }
      .axis { stroke: #dbe6f0; stroke-width: 1.2; }
      .grid { stroke: #dce7f2; stroke-width: 1; stroke-dasharray: 4 5; }
    </style>
  </defs>

  <rect width="1240" height="1754" fill="url(#pageBg)"/>
  <rect x="26" y="26" width="1188" height="1702" rx="30" fill="url(#sheet)"/>

  <g transform="translate(58,56)">
    <rect x="0" y="0" width="1124" height="188" rx="24" fill="url(#hero)"/>
    <text class="h1" x="30" y="66">PM2230 Dynamic Report (Auto Analysis)</text>
    <text class="small" x="30" y="108" fill="#dbeafe">Data source: ${esc(API_BASE)} | Valid samples: ${dataRowsCount}</text>
    <text class="small" x="30" y="136" fill="#dbeafe">Timestamp: ${esc(thTime)} | Device status: ${esc(data.page1.status)}</text>
    <rect x="840" y="112" width="250" height="56" rx="28" fill="#dcfce7"/>
    <text style="font:700 15px 'Segoe UI'; fill:#166534" x="965" y="134" text-anchor="middle">Overall Health Index</text>
    <text style="font:800 24px 'Segoe UI'; fill:#14532d" x="965" y="160" text-anchor="middle">${analysis.scores.overall} / 100</text>
  </g>

  <g transform="translate(58,270)">
    <rect x="0" y="0" width="268" height="142" rx="18" fill="#fff" stroke="#d6e2ee"/>
    <rect x="286" y="0" width="268" height="142" rx="18" fill="#fff" stroke="#d6e2ee"/>
    <rect x="572" y="0" width="268" height="142" rx="18" fill="#fff" stroke="#d6e2ee"/>
    <rect x="858" y="0" width="266" height="142" rx="18" fill="#fff" stroke="#d6e2ee"/>

    <text class="label" x="20" y="36">Active Power (P_Total)</text>
    <text class="value" x="20" y="88">${fmt(data.page2.P_Total, 2)} kW</text>
    <text class="small" x="20" y="116">Peak window: ${fmt(trendStats.maxVal, 2)} kW</text>

    <text class="label" x="306" y="36">Power Factor</text>
    <text class="value" x="306" y="88">${fmt(analysis.pf, 3)}</text>
    <text class="small" x="306" y="116">Target ≥ 0.95</text>

    <text class="label" x="592" y="36">THDv / THDi</text>
    <text class="value" x="592" y="88">${fmt(analysis.thdv, 2)}% / ${fmt(analysis.thdi, 2)}%</text>
    <text class="small" x="592" y="116">Quality watch threshold</text>

    <text class="label" x="878" y="36">Energy Ratio</text>
    <text class="value" x="878" y="88">${fmt(analysis.energyPf, 3)}</text>
    <text class="small" x="878" y="116">kWh/kVAh | Q ratio ${fmt(analysis.reactiveRatio, 2)}</text>
  </g>

  <g transform="translate(58,432)">
    <rect x="0" y="0" width="750" height="456" rx="20" fill="#fff" stroke="#d6e2ee"/>
    <text class="h2" x="24" y="40">Load Trend + Stability Analysis</text>
    <text class="small" x="24" y="64">Trend from data log (auto fallback to synthetic if source is sparse)</text>

    <line class="axis" x1="34" y1="388" x2="716" y2="388"/>
    <line class="grid" x1="34" y1="328" x2="716" y2="328"/>
    <line class="grid" x1="34" y1="268" x2="716" y2="268"/>
    <line class="grid" x1="34" y1="208" x2="716" y2="208"/>
    <line class="grid" x1="34" y1="148" x2="716" y2="148"/>

    <path d="${trendPath.area}" fill="url(#trendFill)"/>
    <path d="${trendPath.line}" fill="none" stroke="#0284c7" stroke-width="4" stroke-linecap="round"/>
    <line x1="34" y1="${(746 - ((trendStats.avgVal - trendStats.minVal) / (trendStats.maxVal - trendStats.minVal || 1)) * 174).toFixed(1)}" x2="716" y2="${(746 - ((trendStats.avgVal - trendStats.minVal) / (trendStats.maxVal - trendStats.minVal || 1)) * 174).toFixed(1)}" stroke="#16a34a" stroke-width="2.5" stroke-dasharray="8 6"/>

    <rect x="24" y="404" width="702" height="34" rx="10" fill="#f8fbff" stroke="#dce7f2"/>
    <text class="small" x="36" y="426">Min ${fmt(trendStats.minVal, 2)}kW</text>
    <text class="small" x="236" y="426">Avg ${fmt(trendStats.avgVal, 2)}kW</text>
    <text class="small" x="410" y="426">Max ${fmt(trendStats.maxVal, 2)}kW</text>
    <text class="small" x="566" y="426">StdDev ${fmt(trendStats.stdVal, 2)}kW</text>

    <rect x="770" y="0" width="354" height="214" rx="20" fill="#fff" stroke="#d6e2ee"/>
    <text class="h3" x="792" y="36">Category Scores</text>

    ${scoreBars.map((row, idx) => {
    const y = 66 + idx * 36;
    const score = row[1];
    return `<text class="small" x="792" y="${y}">${esc(row[0])}</text>
      <rect x="792" y="${y + 6}" width="300" height="10" rx="5" fill="#e2e8f0"/>
      <rect x="792" y="${y + 6}" width="${(score * 3).toFixed(1)}" height="10" rx="5" fill="${scoreColor(score)}"/>
      <text class="small" x="1098" y="${y + 16}" text-anchor="end">${score}</text>`;
  }).join('\n')}

    <rect x="770" y="230" width="354" height="226" rx="20" fill="#fff" stroke="#d6e2ee"/>
    <text class="h3" x="792" y="266">Risk Matrix (Priority)</text>

    ${riskList.map((text, idx) => {
    const y = 286 + idx * 38;
    return `<rect x="792" y="${y}" width="300" height="28" rx="14" fill="${riskFill(idx)}"/>
      <text style="font:700 13px 'Segoe UI'; fill:#fff" x="804" y="${y + 19}">${esc(text)}</text>`;
  }).join('\n')}
  </g>

  <g transform="translate(58,910)">
    <rect x="0" y="0" width="548" height="348" rx="20" fill="#fff" stroke="#d6e2ee"/>
    <rect x="576" y="0" width="548" height="348" rx="20" fill="#fff" stroke="#d6e2ee"/>

    <text class="h3" x="22" y="36">Phase Balance (Voltage/Current)</text>

    <text class="body" x="22" y="82">L1 ${fmt(data.page1.V_LN1, 1)}V | ${fmt(data.page1.I_L1, 2)}A</text>
    <text class="body" x="22" y="138">L2 ${fmt(data.page1.V_LN2, 1)}V | ${fmt(data.page1.I_L2, 2)}A</text>
    <text class="body" x="22" y="194">L3 ${fmt(data.page1.V_LN3, 1)}V | ${fmt(data.page1.I_L3, 2)}A</text>
    <text class="small" x="22" y="246">Current Unbalance: ${fmt(analysis.currentUnbalance, 2)}%</text>
    <text class="small" x="22" y="270">Voltage Avg: ${fmt(analysis.vAvg, 1)}V</text>
    <text class="small" x="22" y="294">Frequency: ${fmt(data.page1.Freq, 2)} Hz</text>

    <rect x="210" y="70" width="318" height="10" rx="5" fill="#e2e8f0"/><rect x="210" y="70" width="${(clamp(data.page1.V_LN1 / 250, 0, 1) * 318).toFixed(1)}" height="10" rx="5" fill="#0ea5e9"/>
    <rect x="210" y="86" width="318" height="10" rx="5" fill="#e2e8f0"/><rect x="210" y="86" width="${(clamp(data.page1.I_L1 / 8, 0, 1) * 318).toFixed(1)}" height="10" rx="5" fill="#6366f1"/>
    <rect x="210" y="126" width="318" height="10" rx="5" fill="#e2e8f0"/><rect x="210" y="126" width="${(clamp(data.page1.V_LN2 / 250, 0, 1) * 318).toFixed(1)}" height="10" rx="5" fill="#0ea5e9"/>
    <rect x="210" y="142" width="318" height="10" rx="5" fill="#e2e8f0"/><rect x="210" y="142" width="${(clamp(data.page1.I_L2 / 8, 0, 1) * 318).toFixed(1)}" height="10" rx="5" fill="#6366f1"/>
    <rect x="210" y="182" width="318" height="10" rx="5" fill="#e2e8f0"/><rect x="210" y="182" width="${(clamp(data.page1.V_LN3 / 250, 0, 1) * 318).toFixed(1)}" height="10" rx="5" fill="#0ea5e9"/>
    <rect x="210" y="198" width="318" height="10" rx="5" fill="#e2e8f0"/><rect x="210" y="198" width="${(clamp(data.page1.I_L3 / 8, 0, 1) * 318).toFixed(1)}" height="10" rx="5" fill="#6366f1"/>

    <text class="h3" x="598" y="36">Power Quality Deep-Dive</text>
    <text class="body" x="598" y="82">THDv Avg: ${fmt(analysis.thdv, 2)}%</text>
    <text class="body" x="598" y="128">THDi Avg: ${fmt(analysis.thdi, 2)}%</text>
    <text class="body" x="598" y="174">PF_Total: ${fmt(analysis.pf, 3)}</text>
    <text class="body" x="598" y="220">Reactive Ratio: ${fmt(analysis.reactiveRatio, 2)}</text>
    <text class="small" x="598" y="264">Observation: metrics generated from latest API snapshot + trend log</text>
    <text class="small" x="598" y="286">Use this as printable one-page summary for operations review</text>

    <rect x="822" y="70" width="300" height="12" rx="6" fill="#e2e8f0"/><rect x="822" y="70" width="${(clamp(analysis.thdv / 8, 0, 1) * 300).toFixed(1)}" height="12" rx="6" fill="#22c55e"/>
    <rect x="822" y="116" width="300" height="12" rx="6" fill="#e2e8f0"/><rect x="822" y="116" width="${(clamp(analysis.thdi / 30, 0, 1) * 300).toFixed(1)}" height="12" rx="6" fill="#f59e0b"/>
    <rect x="822" y="162" width="300" height="12" rx="6" fill="#e2e8f0"/><rect x="822" y="162" width="${(clamp(analysis.pf / 1.0, 0, 1) * 300).toFixed(1)}" height="12" rx="6" fill="#0ea5e9"/>
    <rect x="822" y="208" width="300" height="12" rx="6" fill="#e2e8f0"/><rect x="822" y="208" width="${(clamp(analysis.reactiveRatio / 0.6, 0, 1) * 300).toFixed(1)}" height="12" rx="6" fill="#16a34a"/>
  </g>

  <g transform="translate(58,1280)">
    <rect x="0" y="0" width="1124" height="392" rx="22" fill="#fff" stroke="#d6e2ee"/>
    <text class="h2" x="24" y="40">Detailed Narrative Analysis &amp; Action Plan</text>

    <text class="bullet" x="24" y="78">• Executive Summary: Health score ${analysis.scores.overall}/100, status ${esc(data.page1.status)} with ${analysis.alerts.length} key findings.</text>
    <text class="bullet" x="24" y="106">• Root-Cause hypothesis: ${esc(analysis.thdi > 15 ? 'non-linear load behavior drives harmonic current rise at peak periods.' : 'load profile stable; no major non-linear disturbance detected.')}</text>
    <text class="bullet" x="24" y="144">• Key Alert 1: ${esc(riskList[0])}</text>
    <text class="bullet" x="24" y="172">• Key Alert 2: ${esc(riskList[1])}</text>

    <text class="bullet" x="24" y="210">• Immediate Actions (0-7 days):</text>
    ${analysis.recommendations.slice(0, 5).map((line, idx) => `<text class="bullet" x="44" y="${238 + idx * 28}">${idx + 1}) ${esc(line)}</text>`).join('\n')}

    <rect x="726" y="62" width="374" height="296" rx="16" fill="#f8fbff" stroke="#d6e2ee"/>
    <text class="h3" x="744" y="96">Analysis Confidence</text>
    <text class="small" x="744" y="126">Data Completeness</text>
    <rect x="744" y="136" width="330" height="12" rx="6" fill="#e2e8f0"/><rect x="744" y="136" width="${(analysis.confidence.completeness * 3.3).toFixed(1)}" height="12" rx="6" fill="#16a34a"/>
    <text class="small" x="1080" y="146" text-anchor="end">${analysis.confidence.completeness}%</text>

    <text class="small" x="744" y="176">Model Certainty</text>
    <rect x="744" y="186" width="330" height="12" rx="6" fill="#e2e8f0"/><rect x="744" y="186" width="${(analysis.confidence.certainty * 3.3).toFixed(1)}" height="12" rx="6" fill="#0ea5e9"/>
    <text class="small" x="1080" y="196" text-anchor="end">${analysis.confidence.certainty}%</text>

    <text class="small" x="744" y="226">Actionability</text>
    <rect x="744" y="236" width="330" height="12" rx="6" fill="#e2e8f0"/><rect x="744" y="236" width="${(analysis.confidence.actionability * 3.3).toFixed(1)}" height="12" rx="6" fill="#22c55e"/>
    <text class="small" x="1080" y="246" text-anchor="end">${analysis.confidence.actionability}%</text>

    <text class="small" x="744" y="286">Autogenerated from API + log, ready for A4 export</text>
    <text class="mono" x="744" y="312">Template: PM2230-DYNAMIC-A4-v1</text>
    <text class="mono" x="744" y="334">Source: ${esc(API_BASE)}</text>
  </g>
</svg>`;
}

async function main() {
  const [page1, page2, page3, page4] = await Promise.all([
    fetchJson(`${API_BASE}/api/page1`),
    fetchJson(`${API_BASE}/api/page2`),
    fetchJson(`${API_BASE}/api/page3`),
    fetchJson(`${API_BASE}/api/page4`),
  ]);

  const csvText = await fetchCsv(`${API_BASE}/api/log/download`);
  const rows = parseCsvRows(csvText || '');
  const latestRow = findLatestUsableRow(rows);
  const logPages = buildPagesFromRow(latestRow);
  const trend = trendFromRows(rows);

  const payload = {
    page1: resolvePage(
      page1,
      logPages?.page1,
      sample.page1,
      ['V_LN1', 'V_LN2', 'V_LN3', 'Freq', 'I_L1', 'I_L2', 'I_L3']
    ),
    page2: resolvePage(
      page2,
      logPages?.page2,
      sample.page2,
      ['P_Total', 'P_L1', 'P_L2', 'P_L3', 'S_Total']
    ),
    page3: resolvePage(
      page3,
      logPages?.page3,
      sample.page3,
      ['THDv_L1', 'THDi_L1', 'PF_Total', 'I_unb']
    ),
    page4: resolvePage(
      page4,
      logPages?.page4,
      sample.page4,
      ['kWh_Total', 'kVAh_Total', 'kvarh_Total']
    ),
  };

  const trendStats = {
    minVal: Math.min(...trend),
    maxVal: Math.max(...trend),
    avgVal: avg(trend),
    stdVal: std(trend),
    last: trend[trend.length - 1],
  };

  const analysis = buildAnalysis(payload, trendStats, rows.length);
  const svg = buildSvg(payload, trend, trendStats, analysis, rows.length);

  await fs.mkdir(path.dirname(OUTPUT_FILE), { recursive: true });
  await fs.writeFile(OUTPUT_FILE, svg, 'utf8');

  const meta = {
    output: OUTPUT_FILE,
    source: API_BASE,
    timestamp: new Date().toISOString(),
    usedRows: rows.length,
    overallScore: analysis.scores.overall,
    status: payload.page1.status,
  };

  await fs.writeFile(
    OUTPUT_FILE.replace(/\.svg$/i, '.json'),
    JSON.stringify(meta, null, 2),
    'utf8'
  );

  console.log(`Generated: ${OUTPUT_FILE}`);
  console.log(`Meta: ${OUTPUT_FILE.replace(/\.svg$/i, '.json')}`);
}

main().catch((err) => {
  console.error('Failed to generate report SVG:', err);
  process.exit(1);
});
