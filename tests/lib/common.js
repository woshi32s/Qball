// UI 套件公共库:浏览器启动 + 断言计数 + 产物目录
const path = require('path');
const fs = require('fs');
const pw = require('playwright-core');

const CHANNEL = process.env.PW_CHANNEL || 'msedge';

function makeLogger() {
  let fails = 0;
  function log(ok, name, extra) {
    console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (extra !== undefined ? '  (' + extra + ')' : ''));
    if (!ok) fails++;
  }
  return { log, failCount: () => fails };
}

async function launch() {
  return pw.chromium.launch({ channel: CHANNEL, headless: true });
}

function artifacts(name) {
  const dir = path.join(__dirname, '..', 'artifacts', name);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

module.exports = { pw, launch, makeLogger, artifacts, CHANNEL };
