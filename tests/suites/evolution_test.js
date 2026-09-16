// 自我进化引擎测试:一代完整循环(派活→执行→评审→反思→提案→影子/采纳/测试门)+ 控制接口
// 运行: node suites/evolution_test.js
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const A = process.env.EB_ADMIN || 'http://127.0.0.1:8462';
const STATE = path.join(__dirname, '..', '.runtime', 'home_8462');
const EVO = path.join(STATE, 'evolution');
const APP_DIR = path.join(__dirname, '..', '.runtime', 'app');
const H = { 'Content-Type': 'application/json' };
let fails = 0;
function log(ok, name, extra) {
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (extra !== undefined ? '  (' + extra + ')' : ''));
  if (!ok) fails++;
}
async function j(url, opts) {
  try {
    const r = await fetch(url, opts);
    let d = null;
    try { d = await r.json(); } catch (e) {}
    return { status: r.status, d };
  } catch (e) {
    return { status: 0, d: null, err: String(e) };
  }
}
function control(action, extra) {
  return j(A + '/api/evolution/control', {
    method: 'POST', headers: H, body: JSON.stringify(Object.assign({ action }, extra || {}))
  });
}
function gitLog(dir) {
  try { return execSync('git -C "' + dir + '" log --oneline -5', { encoding: 'utf8' }); } catch (e) { return ''; }
}
function writeCfg(cfg) {
  fs.mkdirSync(EVO, { recursive: true });
  fs.writeFileSync(path.join(EVO, 'config.json'), JSON.stringify(cfg, null, 2));
}

(async () => {
  const cfg = {
    enabled: false,
    notify_desktop: false,
    executor_model: 'fake-model-alpha',
    judge_model: 'fake-model-alpha',
    shadow_generations: 20,
    cooldown_seconds: 5,
    allow_code_changes: true,
    gate_command: 'python -c "print(\'gate ok\')"',
    app_dir: APP_DIR,
    server_url: A
  };
  writeCfg(cfg);

  /* ---------- 第一代:影子模式 ---------- */
  const r1 = await control('run_once_direct');
  const res1 = r1.d && r1.d.result;
  log(r1.status === 200 && r1.d.ok && res1 && res1.gen === 1, 'gen1 runs via api', JSON.stringify(res1));
  log(res1 && res1.score === 1, 'gen1 verified task scored 1.0', res1 && res1.score);
  log(res1 && res1.shadow === true, 'gen1 in shadow mode');
  const gen1 = JSON.parse(fs.readFileSync(path.join(EVO, 'outcomes', 'gen-0001', 'result.json'), 'utf8'));
  log(gen1.steps.some((s) => s.name === 'shadow'), 'shadow step recorded', gen1.steps.map((s) => s.name).join(','));
  const prop1 = JSON.parse(fs.readFileSync(path.join(EVO, 'outcomes', 'gen-0001', 'proposal.json'), 'utf8'));
  const prop1Keys = Object.keys(prop1.scaffold || {});
  log(prop1Keys.length > 0 && prop1Keys[0] === 'agent/system_prompt_addendum.md',
    'gen1 proposal is scaffold + path normalized', prop1Keys.join(','));
  const addendum = fs.readFileSync(path.join(EVO, 'agent', 'system_prompt_addendum.md'), 'utf8');
  log(addendum.trim() === '', 'shadow: addendum NOT applied');
  const journal = fs.readFileSync(path.join(EVO, 'agent', 'memory', 'journal.md'), 'utf8');
  log(journal.length > 30, 'reflection written to journal');
  const scoreLines = fs.readFileSync(path.join(EVO, 'scores.jsonl'), 'utf8').trim().split('\n').length;
  log(scoreLines === 1, 'score recorded', scoreLines);

  /* ---------- 第二代:代码改动 → 测试门 → 采纳 ---------- */
  cfg.shadow_generations = 0;
  writeCfg(cfg);
  const r2 = await control('run_once_direct');
  const res2 = r2.d && r2.d.result;
  log(res2 && res2.gen === 2 && res2.shadow === false, 'gen2 live (no shadow)', JSON.stringify(res2));
  const readme = fs.readFileSync(path.join(APP_DIR, 'README.md'), 'utf8');
  log(readme.indexOf('evolved by qball') >= 0, 'gen2 code change applied to README');
  log(fs.existsSync(path.join(EVO, 'outcomes', 'gen-0002', 'gate.log')), 'gate ran for code change');
  log(/evo\(gen 2\)/.test(gitLog(APP_DIR)), 'gen2 committed in app repo', gitLog(APP_DIR).trim().split('\n')[0]);
  const gen2res = JSON.parse(fs.readFileSync(path.join(EVO, 'outcomes', 'gen-0002', 'result.json'), 'utf8'));
  log(gen2res.steps.some((s) => s.name === 'scaffold-gated'), 'low-score scaffold gated (step recorded)');
  log(!fs.existsSync(path.join(EVO, 'agent', 'skills', 'should-not-apply.md')),
    'gated scaffold NOT applied to workspace');

  /* ---------- 第三代:技能文件(scaffold 采纳) ---------- */
  const r3 = await control('run_once_direct');
  const res3 = r3.d && r3.d.result;
  log(res3 && res3.gen === 3 && res3.shadow === false, 'gen3 live', JSON.stringify(res3));
  log(fs.existsSync(path.join(EVO, 'agent', 'skills', 'evo-demo.md')), 'gen3 skill file adopted');
  log(/evo\(gen 3\)/.test(gitLog(EVO)), 'gen3 committed in evolution repo');

  /* ---------- 派活:第 3 代应生成一个新任务 ---------- */
  const taskDir = path.join(EVO, 'bench', 'tasks');
  const taskFiles = fs.readdirSync(taskDir);
  const newTask = taskFiles
    .map((f) => JSON.parse(fs.readFileSync(path.join(taskDir, f), 'utf8')))
    .find((t) => t.created_by === 'agent');
  log(taskFiles.length >= 5 && !!newTask && newTask.kind === 'verify' && !!newTask.verify,
    'agent generated a new task', newTask && newTask.title);

  /* ---------- 回滚:两次 revert 依次撤回采纳 ---------- */
  const rv1 = await control('revert');
  log(rv1.status === 200 && rv1.d && rv1.d.ok, 'revert #1 ok', rv1.d && rv1.d.message);
  log(!fs.existsSync(path.join(EVO, 'agent', 'skills', 'evo-demo.md')), 'gen3 skill reverted');
  const rv2 = await control('revert');
  log(rv2.status === 200 && rv2.d && rv2.d.ok, 'revert #2 ok', rv2.d && rv2.d.message);
  const readme2 = fs.readFileSync(path.join(APP_DIR, 'README.md'), 'utf8');
  log(readme2.indexOf('evolved by qball') < 0, 'gen2 code change reverted');
  const rv3 = await control('revert');
  log(rv3.status === 400, 'nothing left to revert -> 400');

  /* ---------- 知识合并(维护代):长附加指令 + 多个技能 → 自动合并 ---------- */
  fs.mkdirSync(path.join(EVO, 'agent', 'skills'), { recursive: true });
  fs.writeFileSync(path.join(EVO, 'agent', 'skills', 'tmp-a.md'), '# A\n旧技能一\n');
  fs.writeFileSync(path.join(EVO, 'agent', 'skills', 'tmp-b.md'), '# B\n旧技能二\n');
  fs.writeFileSync(path.join(EVO, 'agent', 'system_prompt_addendum.md'), 'X'.repeat(2000));
  const r4 = await control('run_once_direct');
  const res4 = r4.d && r4.d.result;
  log(res4 && res4.gen === 4 && typeof res4.consolidate === 'string', 'consolidation generation runs',
    res4 && (res4.consolidate || '').slice(0, 60));
  const gen4res = JSON.parse(fs.readFileSync(path.join(EVO, 'outcomes', 'gen-0004', 'result.json'), 'utf8'));
  log(gen4res.steps.some((s) => s.name === 'consolidate'), 'consolidate step recorded');
  const addendum4 = fs.readFileSync(path.join(EVO, 'agent', 'system_prompt_addendum.md'), 'utf8');
  log(addendum4.length < 1500 && addendum4.indexOf('核心纪律') >= 0, 'addendum consolidated (<1500 chars)', addendum4.length);
  const skills4 = fs.readdirSync(path.join(EVO, 'agent', 'skills'));
  log(skills4.length === 1 && skills4[0] === 'combined-discipline.md', 'skills merged to one file', skills4.join(','));
  log(/consolidate knowledge/.test(gitLog(EVO)), 'consolidation committed in evolution repo');

  /* ---------- 状态与控制接口 ---------- */
  const st = await j(A + '/api/evolution/status');
  log(st.d && st.d.generation === 4 && st.d.recent.length >= 4,
    'status: generation + recent scores', JSON.stringify({ gen: st.d.generation, recent: st.d.recent.length }));
  log(st.d && st.d.source_mode === true, 'status: source mode detected');
  log(st.d && st.d.executor_model === 'fake-model-alpha', 'status: models from config', st.d.executor_model);
  await control('pause');
  log((await j(A + '/api/evolution/status')).d.paused === true, 'pause flag set');
  await control('resume');
  log((await j(A + '/api/evolution/status')).d.paused === false, 'resume flag cleared');
  const models = await control('set_models', { executor_model: 'fake-model-beta' });
  const st2 = await j(A + '/api/evolution/status');
  log(models.status === 200 && st2.d.executor_model === 'fake-model-beta', 'set_models works', st2.d.executor_model);
  await control('set_models', { executor_model: 'fake-model-alpha' });

  /* ---------- 体验改进通道:ux 任务 + 功能地图 + 报告收集 + 通知开关 ---------- */
  const allTasks = fs.readdirSync(path.join(EVO, 'bench', 'tasks'))
    .map((f) => JSON.parse(fs.readFileSync(path.join(EVO, 'bench', 'tasks', f), 'utf8')));
  const uxSeed = allTasks.find((t) => t.id === 'seed-ux-firstrun');
  log(!!uxSeed && uxSeed.kind === 'ux' && allTasks.some((t) => t.id === 'seed-ux-shortcuts'),
    'ux seed tasks present', allTasks.filter((t) => t.kind === 'ux').map((t) => t.id).join(','));

  const rootDir = path.join(__dirname, '..', '..');
  const probeFile = path.join(EVO, 'ux_probe.py');
  fs.writeFileSync(probeFile, [
    'import sys, json, pathlib',
    'sys.path.insert(0, r"' + rootDir + '")',
    'import evolution as E',
    'cfg = {"_app_dir": r"' + APP_DIR + '", "notify_desktop": False}',
    'digest = E.ui_digest(cfg)',
    'state = pathlib.Path(r"' + STATE + '")',
    'ws = state / "workspace"',
    'ws.mkdir(parents=True, exist_ok=True)',
    '(ws / "ux-ideas.md").write_text("- 测试按钮A | 找不到入口 | 加到设置顶部 | qball.html | 点开可见\\n"',
    '    "- 测试按钮B | 太绕 | 收进菜单 | qball.html | 一次点击\\n", encoding="utf-8")',
    'n1 = E.harvest_ux_ideas(state, 99, 0.8, {"text": ""})',
    'n2 = E.harvest_ux_ideas(state, 99, 0.8, {"text": ""})',
    'report = E.paths(state)["root"] / "reports" / "ux-ideas.md"',
    'txt = report.read_text(encoding="utf-8") if report.exists() else ""',
    'notify_off = E.notify_desktop(cfg, "t", "m")',
    'class StubSrv:',
    '    calls = 0',
    '    def chat_stream(self, prompt, model, sid, timeout=600):',
    '        StubSrv.calls += 1',
    '        (ws / "ux-ideas.md").write_text("- 补写按钮 | 痛点X | 做法Y | qball.html | 能点\\n", encoding="utf-8")',
    '        return {"text": "OK", "tools": [{"name": "fs.write", "ok": True, "text": ""}]}',
    '(ws / "ux-ideas.md").unlink()',
    'tr = {"text": "", "tools": []}',
    'did1 = E.nudge_missing_artifact(state, {"executor_model": "m"}, StubSrv(), 98, {"kind": "ux"}, tr)',
    'did2 = E.nudge_missing_artifact(state, {"executor_model": "m"}, StubSrv(), 98, {"kind": "ux"}, tr)',
    'did3 = E.nudge_missing_artifact(state, {"executor_model": "m"}, StubSrv(), 98, {"kind": "verify"}, tr)',
    'print(json.dumps({"digest": len(digest), "has_tool": "fs.write" in digest,',
    '                  "has_ui": "界面按钮" in digest, "n1": n1, "n2": n2,',
    '                  "has_a": "测试按钮A" in txt, "has_b": "测试按钮B" in txt,',
    '                  "header": "体验改进报告" in txt, "notify_off": notify_off,',
    '                  "nudge": [did1, did2, did3, StubSrv.calls, [t["name"] for t in tr["tools"]]]}))',
  ].join('\n'), 'utf8');
  const probe = JSON.parse(execSync('python "' + probeFile + '"', { encoding: 'utf8' }));
  log(probe.has_tool && probe.has_ui && probe.digest > 60, 'ui_digest builds feature map', probe.digest);
  log(probe.n1 === 2 && probe.n2 === 0 && probe.has_a && probe.has_b && probe.header,
    'harvest: report written + dedupe', JSON.stringify({ n1: probe.n1, n2: probe.n2 }));
  log(probe.notify_off === false, 'notify disabled -> no-op');
  log(probe.nudge[0] === true && probe.nudge[1] === false && probe.nudge[2] === false &&
    probe.nudge[3] === 1 && probe.nudge[4].indexOf('fs.write') >= 0,
    'nudge: retries once when artifact missing', JSON.stringify(probe.nudge));

  const lib = await j(A + '/api/evolution/library');
  log(lib.status === 200 && lib.d.available && Array.isArray(lib.d.skills) && typeof lib.d.ideas === 'string',
    'library api shape', JSON.stringify({ skills: lib.d && lib.d.skills && lib.d.skills.length }));
  log((lib.d.ideas || '').indexOf('测试按钮A') >= 0, 'library api returns collected ideas');
  const sn = await control('set_notify', { value: false });
  log(sn.status === 200 && sn.d.notify_desktop === false, 'set_notify works');
  await control('set_notify', { value: true });

  /* ---------- UI:设置里的自我进化面板 ---------- */
  const { launch } = require('../lib/common');
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  page.on('pageerror', (e) => console.log('PAGE-EXC', e.message));
  await page.addInitScript((fakeBase) => {
    try {
      localStorage.setItem('qball.muted', '1');
      localStorage.setItem('qball.api.v1', JSON.stringify({ base: fakeBase, key: 'sk-fake', model: 'fake-model-alpha' }));
    } catch (e) {}
  }, A + '/v1');
  await page.goto(A + '/qball.html?mock=1', { waitUntil: 'load' });
  await page.waitForTimeout(2200);
  await page.click('#settings-handle');
  await page.waitForFunction(() => {
    const el = document.getElementById('evo-title');
    return el && el.style.display !== 'none';
  }, null, { timeout: 10000 }).catch(() => {});
  const evo = await page.evaluate(() => ({
    title: document.getElementById('evo-title').style.display !== 'none',
    status: document.getElementById('evo-status').textContent,
    toggle: !!document.getElementById('set-evo'),
    skillsBtn: !!document.getElementById('evo-skills'),
    ideasBtn: !!document.getElementById('evo-ideas'),
    notifyInput: !!document.getElementById('set-evo-notify')
  }));
  log(evo.title && /代/.test(evo.status), 'UI: evolution panel shows status', evo.status.trim().slice(0, 60));
  log(evo.skillsBtn && evo.ideasBtn && evo.notifyInput, 'UI: library + notify controls exist');
  await browser.close();

  console.log(fails === 0 ? 'ALL PASS' : fails + ' FAILURES');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
